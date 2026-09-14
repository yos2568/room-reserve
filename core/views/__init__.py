"""View modules.

Imported here so ``roomreserve.urls`` can address them as ``views.<module>``.
"""

from core.views import booking, content, health, identity, public, staff

__all__ = ["booking", "content", "health", "identity", "public", "staff"]
