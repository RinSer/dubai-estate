"""Settings for the MCP server.

Same plain-dataclass style as the ELT and API configs (no pydantic-settings),
so all three services read env the same way and one habit covers the platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _get(name: str, default: str) -> str:
    # .strip() mirrors the other two loaders: some .env consumers keep trailing
    # whitespace that Docker Compose's parser would have trimmed.
    v = os.environ.get(name, "").strip()
    return v if v != "" else default


def _bool(name: str, default: str) -> bool:
    return _get(name, default) not in ("0", "false", "no", "off")


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in _get(name, default).split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    # --- upstream REST API ---
    api_base_url: str
    api_key: str
    api_timeout_seconds: float

    # --- transport ---
    host: str
    port: int
    path: str

    # stdio is a debugging convenience and a second command channel on a public
    # host. Off unless explicitly switched on (docs/MCP_DESIGN.md §3).
    stdio_enabled: bool

    # DNS-rebinding protection. The SDK validates Host and Origin, which stops
    # a malicious web page from driving an MCP server reachable from the
    # victim's machine. Behind a reverse proxy the Host is whatever the client
    # sent, so the public hostnames have to be listed here or every proxied
    # request is rejected with "Invalid Host header".
    #
    # Kept ON with an explicit list rather than disabled — the check is cheap
    # and the attack it prevents is real.
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]

    log_level: str


@lru_cache
def get_settings() -> Settings:
    return build_settings()


def build_settings() -> Settings:
    return Settings(
        # Service DNS inside the compose network; nginx is in front of the
        # public edge, so this hop stays plain HTTP on the internal network.
        api_base_url=_get("DXB_MCP_API_URL", "http://api:8000").rstrip("/"),
        api_key=_get("DXB_MCP_API_KEY", ""),
        # Generous relative to a typical request: some analytics queries scan
        # years of marts, and an agent waiting is better than an agent
        # inventing an answer because the tool errored.
        api_timeout_seconds=float(_get("DXB_MCP_API_TIMEOUT", "30")),
        host=_get("DXB_MCP_HOST", "0.0.0.0"),
        port=int(_get("DXB_MCP_PORT", "8100")),
        path=_get("DXB_MCP_PATH", "/mcp"),
        stdio_enabled=_bool("DXB_MCP_STDIO", "0"),
        # Defaults cover local development through the nginx edge. A real
        # deployment must add its own hostname — see mcp/README.md.
        allowed_hosts=_csv(
            "DXB_MCP_ALLOWED_HOSTS", "localhost,localhost:443,localhost:8100,127.0.0.1"
        ),
        allowed_origins=_csv(
            "DXB_MCP_ALLOWED_ORIGINS", "https://localhost,http://localhost"
        ),
        log_level=_get("DXB_MCP_LOG_LEVEL", "INFO").upper(),
    )
