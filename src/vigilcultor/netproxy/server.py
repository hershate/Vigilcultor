"""Threaded allowlist HTTP proxy (absolute-URI requests and CONNECT tunnels)."""

from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DENY_MESSAGE = b"HTTP/1.1 403 Forbidden by egress policy\r\nContent-Length: 0\r\n\r\n"
TUNNEL_BUFFER = 65536
TUNNEL_TIMEOUT = 300


def host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    """Exact match or dot-suffix match (api.github.com matches github.com)."""
    host = host.split(":")[0].lower()
    for allowed in allowlist:
        allowed = allowed.lower().lstrip(".")
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


class AuditLog:
    """Append-only JSONL audit trail; one line per proxied or denied request."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def record(
        self, *, method: str, host: str, allowed: bool, status: str, bidirectional: int = 0
    ) -> None:
        line = json.dumps(
            {
                "ts": round(time.time(), 3),
                "method": method,
                "host": host,
                "allowed": allowed,
                "status": status,
                "bytes": bidirectional,
            },
            ensure_ascii=False,
        )
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class _ProxyHandler(socketserver.BaseRequestHandler):
    allowlist: list[str] = []
    audit: AuditLog | None = None

    def handle(self) -> None:
        # Read the full head (request line + headers) in one buffered pass;
        # anything after \r\n\r\n is (potential) request body kept for forwarding.
        data = b""
        while b"\r\n\r\n" not in data:
            try:
                chunk = self.request.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        parts = lines[0].decode("latin-1", "replace").split()
        if len(parts) != 3:
            return
        method, target, _version = parts
        if method == "CONNECT":
            self._handle_connect(method, target)
        else:
            self._handle_http(method, target, head, body)

    def _connect_upstream(self, host: str, port: int) -> socket.socket | None:
        try:
            return socket.create_connection((host, port), timeout=30)
        except OSError:
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return None

    def _audit(self, method: str, host: str, allowed: bool) -> None:
        if self.audit:
            self.audit.record(
                method=method,
                host=host,
                allowed=allowed,
                status="allow" if allowed else "deny",
            )

    def _handle_connect(self, method: str, target: str) -> None:
        allowed = host_allowed(target, self.allowlist)
        self._audit(method, target, allowed)
        if not allowed:
            self.request.sendall(DEFAULT_DENY_MESSAGE)
            return
        host = target.split(":")[0]
        port = int(target.split(":")[1]) if ":" in target else 443
        upstream = self._connect_upstream(host, port)
        if upstream is None:
            return
        self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self._splice(self.request, upstream)

    def _handle_http(self, method: str, target: str, head: bytes, body: bytes) -> None:
        split = urlsplit(target)
        host_port = split.netloc
        allowed = host_allowed(host_port, self.allowlist)
        self._audit(method, host_port, allowed)
        if not allowed:
            self.request.sendall(DEFAULT_DENY_MESSAGE)
            return
        host = host_port.split(":")[0]
        port = int(host_port.split(":")[1]) if ":" in host_port else 80
        upstream = self._connect_upstream(host, port)
        if upstream is None:
            return
        # Rebuild in origin-form: request line points at the path, header block
        # (minus original request line) kept verbatim, body bytes re-appended.
        headers_only = head.split(b"\r\n", 1)[1]
        path = split.path or "/"
        if split.query:
            path += "?" + split.query
        upstream.sendall(
            f"{method} {path} HTTP/1.1\r\n".encode("latin-1")
            + headers_only
            + b"\r\n\r\n"
            + body
        )
        self._splice(self.request, upstream)

    def _splice(self, client: socket.socket, upstream: socket.socket) -> None:
        client.settimeout(TUNNEL_TIMEOUT)
        upstream.settimeout(TUNNEL_TIMEOUT)

        def pump(source: socket.socket, sink: socket.socket) -> int:
            moved = 0
            try:
                while True:
                    chunk = source.recv(TUNNEL_BUFFER)
                    if not chunk:
                        break
                    sink.sendall(chunk)
                    moved += len(chunk)
            except OSError:
                pass
            finally:
                try:
                    sink.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            return moved

        down = threading.Thread(target=pump, args=(upstream, client))
        down.start()
        total = pump(client, upstream)
        down.join(timeout=TUNNEL_TIMEOUT)
        if self.audit:
            self.audit.record(
                method="TUNNEL",
                host="",
                allowed=True,
                status="closed",
                bidirectional=total,
            )


class EgressProxyServer:
    def __init__(self, allowlist: list[str], audit_path: Path) -> None:
        audit = AuditLog(audit_path)
        handler = type(
            "BoundHandler",
            (_ProxyHandler,),
            {"allowlist": list(allowlist), "audit": audit},
        )
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
