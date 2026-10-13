"""Template context shared by every page."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

# Bumped when the rules copy changes; recorded on the account when acknowledged.
RULES_VERSION = "2026-09-15"

# Which collapsible group of the staff menu holds each staff page, so the group
# containing the current page opens by itself.
STAFF_NAV_GROUPS = {
    "staff_closures": "operations",
    "staff_calendar": "operations",
    "staff_incidents": "operations",
    "staff_roster": "operations",
    "staff_room_access": "operations",
    "staff_invitations": "records",
    "staff_audit": "records",
    "staff_stats": "records",
    "staff_posters": "records",
    "staff_policy": "records",
    "staff_admin": "records",
}


def site_context(request):
    """Contact details, policy values and the current Bangkok date.

    Deliberately free of any student identity: this context is rendered on the
    public grid as well (V3 section 9).
    """
    return {
        "RULES_VERSION": RULES_VERSION,
        "support_contact": {
            "name": settings.SUPPORT_CONTACT_NAME,
            "phone": settings.SUPPORT_CONTACT_PHONE,
            "email": settings.SUPPORT_CONTACT_EMAIL,
        },
        "policy": {
            "horizon_days": settings.HORIZON_DAYS,
            "daily_quota": settings.DAILY_QUOTA,
            "grace_minutes": settings.CHECKIN_GRACE_MINUTES,
            "strike_window_days": settings.STRIKE_WINDOW_DAYS,
            "strike_threshold": settings.STRIKE_THRESHOLD,
            "auto_suspension_days": settings.AUTO_SUSPENSION_DAYS,
            "opening_hour": settings.OPENING_SLOT_START_HOUR,
            "last_slot_hour": settings.LAST_SLOT_START_HOUR,
            "closing_hour": settings.LAST_SLOT_START_HOUR + 1,
        },
        "today": timezone.localdate(),
        "staff_nav_open": STAFF_NAV_GROUPS.get(
            getattr(getattr(request, "resolver_match", None), "url_name", None)
        ),
    }
