"""Egress proxy tests: allowlist matching, forwarding, denial, audit log."""

from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import httpx
import pytest

from vigilcultor.netproxy.server import EgressProxyServer, host_allowed


class TestHostAllowed:
    def test_suffix_matching(self):
        allow = ["github.com", "pypi.org"]
        assert host_allowed("github.com", allow)
        assert host_allowed("api.github.com", allow)
        assert host_allowed("FILES.PYTHONHOSTED.ORG:443", ["pypi.org"]) is False
        assert host_allowed("evil.com", allow) is False
        assert host_allowed("notgithub.com", allow) is False  # no prefix tricks


@pytest.fixture()
def origin_server():
    """A plain HTTP origin answering /ping."""
    handler = type("Echo", (http.server.BaseHTTPRequestHandler,), {
        "do_GET": lambda self: (
            self.send_response(200),
            self.send_header("Content-Length", "4"),
            self.end_headers(),
            self.wfile.write(b"pong"),
        ),
        "log_message": lambda *args, **kwargs: None,
    })
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture()
def proxy(tmp_path: Path):
    server = EgressProxyServer(["127.0.0.1"], tmp_path / "netproxy.jsonl")
    server.start()
    yield server
    server.stop()


def read_audit(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "netproxy.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_http_forward_allowed(tmp_path, origin_server, proxy):
    url = f"http://127.0.0.1:{origin_server.server_address[1]}/ping"
    client = httpx.Client(proxy=f"http://127.0.0.1:{proxy.port}")
    response = client.get(url)
    assert response.status_code == 200
    assert response.text == "pong"
    audit = read_audit(tmp_path)
    assert any(entry["allowed"] and entry["method"] == "GET" for entry in audit)


def test_http_denied_and_audited(tmp_path, origin_server, proxy):
    # Destination host is 127.0.0.1 (allowlisted), but we send to a *blocked*
    # host by using a request to an external-looking host that resolves to the
    # local server — simulate via raw socket with absolute URI of blocked host.
    blocked_port = origin_server.server_address[1]
    raw = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    request = (
        f"GET http://blocked.example:{blocked_port}/ping HTTP/1.1\r\n"
        f"Host: blocked.example:{blocked_port}\r\nConnection: close\r\n\r\n"
    )
    raw.sendall(request.encode())
    data = raw.recv(4096)
    raw.close()
    assert b"403" in data
    audit = read_audit(tmp_path)
    assert any(
        not entry["allowed"] and "blocked.example" in entry["host"] for entry in audit
    )


def test_connect_denied_for_blocked_host(tmp_path, proxy):
    raw = socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
    raw.sendall(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example:443\r\n\r\n")
    data = raw.recv(4096)
    raw.close()
    assert b"403" in data
    audit = read_audit(tmp_path)
    assert any(entry["method"] == "CONNECT" and not entry["allowed"] for entry in audit)


def test_bind_host_exposed(tmp_path):
    from vigilcultor.netproxy.server import EgressProxyServer

    server = EgressProxyServer(["example.com"], tmp_path / "a.jsonl", bind_host="127.0.0.1")
    server.start()
    try:
        assert server.bind_host == "127.0.0.1"
        assert server.proxy_url == f"http://127.0.0.1:{server.port}"
    finally:
        server.stop()


def test_default_bind_host_falls_back(monkeypatch):
    from vigilcultor.netproxy import server as srv

    def boom(*args, **kwargs):
        raise FileNotFoundError("no podman")

    monkeypatch.setattr(srv.subprocess, "run", boom)
    assert srv.default_bind_host() == "127.0.0.1"
