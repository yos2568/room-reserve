"""Audit-trail writes.

Every operational service records one event here. ``changes`` holds only safe,
non-secret values: never a password, token or full email body.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.middleware import current_correlation_id
from core.models import AuditEvent

# Any key matching these fragments is dropped before it reaches the database.
_REDACT_KEYS = ("password", "token", "secret", "otp", "apikey", "api_key")


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if any(fragment in str(key).lower() for fragment in _REDACT_KEYS)
                else _scrub(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_scrub(item, depth + 1) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def record_audit(
    *,
    action: str,
    entity_type: str,
    entity_id: Any = "",
    actor=None,
    actor_label: str = "",
    changes: dict[str, Any] | None = None,
    reason: str = "",
    correlation_id=None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id="" if entity_id == "" else str(entity_id),
        changes=_scrub(changes or {}),
        reason=reason,
        correlation_id=correlation_id or current_correlation_id() or uuid.uuid4(),
    )
