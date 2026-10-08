"""Default egress allowlist for task containers (extendable via kv/netproxy:allowlist)."""

from __future__ import annotations

from vigilcultor.store.kv import kv_get

ALLOWLIST_KV_KEY = "netproxy:allowlist"

DEFAULT_ALLOWLIST: tuple[str, ...] = (
    # GitHub
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "githubusercontent.com",
    # Package ecosystems
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "proxy.golang.org",
    # Base image registries + CN mirrors (this host cannot reach docker.io directly)
    "docker.io",
    "registry-1.docker.io",
    "docker.m.daocloud.io",
    "docker.1ms.run",
)


def effective_allowlist() -> list[str]:
    """默认清单 ∪ kv 扩展项（kv 只增不减，避免覆盖掉 GitHub/包生态必需项）。"""
    try:
        configured = kv_get(ALLOWLIST_KV_KEY)
    except Exception:  # noqa: BLE001 - uninitialized database falls back to defaults
        configured = None
    merged = list(DEFAULT_ALLOWLIST)
    if isinstance(configured, list):
        for host in configured:
            name = str(host).lower().strip()
            if name and name not in merged:
                merged.append(name)
    return merged
