"""Roster entries and single-use invitations."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class EligibleStudent(models.Model):
    """An approved roster entry: the institutional ID and email pair that may book.

    The roster is the authority for department membership. Email ownership alone
    does not establish it (V3 section 8), which is why both columns are unique
    and validated together.
    """

    institutional_id = models.CharField(_("institutional ID"), max_length=64, unique=True)
    email = models.EmailField(_("approved email"), unique=True)
    name_th = models.CharField(_("Thai name"), max_length=200, blank=True)
    name_en = models.CharField(_("English name"), max_length=200, blank=True)
    program = models.CharField(_("programme"), max_length=120, blank=True)
    instrument = models.CharField(_("instrument"), max_length=120, blank=True)
    year = models.PositiveSmallIntegerField(_("year"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    account = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="roster_entries",
    )
    import_batch = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("eligible student")
        verbose_name_plural = _("eligible students")
        ordering = ["institutional_id"]
        indexes = [
            models.Index(fields=["is_active"], name="roster_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.institutional_id} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        self.institutional_id = self.institutional_id.strip()
        super().save(*args, **kwargs)


class Invitation(models.Model):
    """A single-use invitation. Only the digest of the token is persisted."""

    class Purpose(models.TextChoices):
        EMAIL_VERIFICATION = "EMAIL_VERIFICATION", _("Email verification")
        ACCOUNT_ACTIVATION = "ACCOUNT_ACTIVATION", _("Account activation")
        PASSWORD_RESET = "PASSWORD_RESET", _("Password reset")

    user = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="invitations")
    token_digest = models.CharField(max_length=64)
    generation = models.PositiveIntegerField(default=1)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitations_created",
    )

    class Meta:
        verbose_name = _("invitation")
        verbose_name_plural = _("invitations")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "purpose", "generation"],
                name="invitation_user_purpose_generation_unique",
            ),
            models.UniqueConstraint(
                fields=["token_digest"],
                name="invitation_token_digest_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="invitation_expiry_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} #{self.generation} for {self.user_id}"

    @property
    def is_redeemable(self) -> bool:
        return (
            self.used_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )
