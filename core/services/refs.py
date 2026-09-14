"""Re-reading instances under a row lock.

Views fetch an object, then call a service. Between those two steps another
connection can commit a state change - a scheduled booking can be reconciled to
NO_SHOW, a suspension can be lifted, a closure revoked. Deciding from the
caller's in-memory copy therefore risks acting on stale state and overwriting a
committed fact.

V3 section 5 requires validation "against reconciled state". Every mutation body
that receives a model instance calls :func:`reload_for_update` first, so the
decision and the write are both based on the committed row.
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def reload_for_update(instance: T) -> T:
    """Return the committed row, locked for update.

    Raises the model's ``DoesNotExist`` if the row is gone, which the protocol
    reports as a clean rejection rather than a server error.
    """
    model = type(instance)
    return model._default_manager.select_for_update().get(pk=instance.pk)
