"""Small security helpers shared by request guards."""

from __future__ import annotations

import ipaddress


def client_ip(request) -> str:
    """Return the address supplied by the trusted Caddy reverse proxy."""
    return (request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or "").strip()


def client_ip_allowed(request, rules: list[str] | tuple[str, ...]) -> bool:
    """Return whether the request client matches an IP or CIDR allowlist."""
    if not rules:
        return True
    try:
        address = ipaddress.ip_address(client_ip(request))
    except ValueError:
        return False

    for rule in rules:
        try:
            if address in ipaddress.ip_network(rule, strict=False):
                return True
        except ValueError:
            continue
    return False
