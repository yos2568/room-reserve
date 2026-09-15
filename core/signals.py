"""Signal handlers.

The operational-staff group and the ``is_operational_staff`` flag are kept in
step here. The flag is the fast check used by permission helpers and templates;
the group is what an administrator actually edits.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

OPERATIONAL_STAFF_GROUP = "Operational Staff"


def _sync_staff_flag(user) -> None:
    from core.models import User

    if not isinstance(user, User) or user.pk is None:
        return
    is_member = user.groups.filter(name=OPERATIONAL_STAFF_GROUP).exists()
    if user.is_operational_staff != is_member:
        # .update() avoids re-entering save() while inside an m2m signal.
        User.objects.filter(pk=user.pk).update(is_operational_staff=is_member)
        user.is_operational_staff = is_member


@receiver(
    m2m_changed,
    dispatch_uid="core.sync_operational_staff_flag",
)
def _on_group_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    from core.models import User

    if sender is not User.groups.through:
        return

    affected = (
        (User.objects.filter(pk__in=pk_set) if pk_set else User.objects.filter(groups=instance))
        if reverse
        else [instance]
    )

    for user in affected:
        _sync_staff_flag(user)
