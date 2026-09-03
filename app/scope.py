"""Pure authorization policy for the gateway.

Authentication and authorization are separate. GitHub proves identity; this
module decides whether that immutable numeric identity is admitted and which
read-only tools its role may use.
"""
from __future__ import annotations

import os

ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"

CATALOG: dict[str, frozenset[str]] = {
    ROLE_OPERATOR: frozenset({"list_services", "service_details", "search_runbooks"}),
    ROLE_VIEWER: frozenset({"list_services", "service_details"}),
}


class ConfigError(RuntimeError):
    """Raised when authorization configuration cannot be used safely."""


def _ids(env: dict[str, str], name: str) -> list[str]:
    values = [part.strip() for part in env.get(name, "").split(",") if part.strip()]
    for value in values:
        if not value.isdecimal():
            raise ConfigError(f"{name} must contain only numeric GitHub user IDs")
    return values


def load_allowlist(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return an ID-to-role map, refusing empty or ambiguous configuration."""
    env = os.environ if env is None else env
    operators = _ids(env, "GATEWAY_OPERATOR_GITHUB_IDS")
    viewers = _ids(env, "GATEWAY_VIEWER_GITHUB_IDS")

    if not operators:
        raise ConfigError("GATEWAY_OPERATOR_GITHUB_IDS is empty; refusing to start")

    overlap = set(operators) & set(viewers)
    if overlap:
        raise ConfigError("a GitHub ID is assigned to more than one role")

    return {
        **{github_id: ROLE_OPERATOR for github_id in operators},
        **{github_id: ROLE_VIEWER for github_id in viewers},
    }


def role_for(github_id: str | None, allowlist: dict[str, str]) -> str | None:
    """Unknown or missing identities receive no role."""
    if not github_id:
        return None
    return allowlist.get(str(github_id))


def allowed_tools(role: str | None) -> frozenset[str]:
    """Unknown roles receive an empty catalog."""
    return CATALOG.get(role or "", frozenset())


def is_tool_allowed(role: str | None, tool_name: str) -> bool:
    return tool_name in allowed_tools(role)
