"""Egress audit proxy: the only outbound channel granted to task containers.

HTTP/HTTPS forwarding proxy with per-host allowlist and JSONL audit log.
M3 runs it as env-level proxy (HTTP_PROXY/HTTPS_PROXY); strict network-level
enforcement (deny non-proxy egress) is a hardening item tracked in note/.
"""
