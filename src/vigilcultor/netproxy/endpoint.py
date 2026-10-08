"""Share the live proxy endpoint across processes via the kv store.

The netproxy process announces its bind host/port at startup; other processes
(worker, onboarding) read it back and hand containers a URL based on
``host.containers.internal``, which resolves to the host under both the pasta
and netavark backends.
"""

from __future__ import annotations

ENDPOINT_KEY = "netproxy:endpoint"


def announce(bind_host: str, port: int) -> None:
    """Record the live proxy endpoint (called by the netproxy process)."""
    from vigilcultor.store.kv import kv_set

    kv_set(ENDPOINT_KEY, {"bind_host": bind_host, "port": port})


def container_proxy_url() -> str | None:
    """Container-reachable proxy URL, or None when no proxy has announced."""
    from vigilcultor.store.kv import kv_get

    try:
        endpoint = kv_get(ENDPOINT_KEY)
    except Exception:  # noqa: BLE001 - DB not initialised yet
        return None
    if not isinstance(endpoint, dict) or "port" not in endpoint:
        return None
    return f"http://host.containers.internal:{endpoint['port']}"
