"""View modules.

Imported here so ``roomreserve.urls`` can address them as ``views.<module>``.
"""

from core.views import booking, complaints, content, health, identity, public, staff

__all__ = ["booking", "complaints", "content", "health", "identity", "public", "staff"]
