"""User account model.

Design decision (see docs/decisions.md D-02): Django's ``username`` field holds
the institutional login ID and is the single source of truth for it, exposed as
``User.institutional_id``. Storing the same identifier in two columns would let
them diverge, and V3 section 4 only requires that the ID be unique and stored as
a string without assuming a length or inferring it from the email local part.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.identity import InstrumentCategory

# Permissive on purpose: roster IDs are supplied by the faculty and may contain
# characters the default Django username validator rejects. Whitespace is the
# only hard exclusion.
institutional_id_validator = RegexValidator(
    regex=r"^\S+$",
    message=_("Institutional ID must not contain spaces."),
)


class UserManager(BaseUserManager):
    """Manager that normalises the institutional email address."""

    use_in_migrations = True

    def _normalise_email(self, email: str | None) -> str:
        return (email or "").strip().lower()

    def create_user(self, username: str, email: str, password: str | None = None, **extra):
        if not username:
            raise ValueError("The institutional ID is required.")
        if not email:
            raise ValueError("The institutional email address is required.")
        email = self._normalise_email(email)
        user = self.model(username=username.strip(), email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("eligibility", User.Eligibility.APPROVED)
        extra.setdefault("email_verified_at", timezone.now())
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(username, email, password, **extra)


class User(AbstractUser):
    class Locale(models.TextChoices):
        TH = "th", _("Thai")
        EN = "en", _("English")

    class Eligibility(models.TextChoices):
        PENDING = "PENDING", _("Pending review")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")

    # Institutional ID lives in ``username``; see the module docstring.
    username = models.CharField(
        _("institutional ID"),
        max_length=64,
        unique=True,
        validators=[institutional_id_validator],
        help_text=_("Institutional login ID supplied by the faculty roster."),
    )
    email = models.EmailField(_("institutional email"), unique=True)
    name_th = models.CharField(_("Thai name"), max_length=200, blank=True)
    name_en = models.CharField(_("English name"), max_length=200, blank=True)
    locale = models.CharField(
        _("preferred language"),
        max_length=5,
        choices=Locale.choices,
        default=Locale.TH,
    )

    email_verified_at = models.DateTimeField(_("email verified at"), null=True, blank=True)

    eligibility = models.CharField(
        _("eligibility"),
        max_length=16,
        choices=Eligibility.choices,
        default=Eligibility.PENDING,
    )
    eligibility_decided_at = models.DateTimeField(null=True, blank=True)
    eligibility_decided_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eligibility_decisions",
    )
    eligibility_reason = models.TextField(blank=True)

    # Self-declared at registration (D-30). Honoured for restricted-room access
    # only once the account is approved, and overridden by the roster row's
    # derived category the moment a roster link exists.
    declared_category = models.CharField(
        _("declared instrument"),
        max_length=32,
        blank=True,
        choices=InstrumentCategory.choices,
        help_text=_(
            "The instrument family the student declared at registration. A roster "
            "link, when it exists, is authoritative over this value."
        ),
    )

    # Operational staff (ten named accounts) are distinct from the technical
    # maintainer, who is the only superuser (V3 section 1 and section 8).
    is_operational_staff = models.BooleanField(
        _("operational staff"),
        default=False,
        help_text=_("Members of the ten-account staff group; cannot grant superuser."),
    )

    # Faculty accounts (D-34): invited by staff, sign in with email or ID, and
    # are read-only for now — they see schedules but cannot reserve rooms.
    is_teacher = models.BooleanField(
        _("teacher"),
        default=False,
        help_text=_("Faculty account. Read-only today; permissions widen later."),
    )

    rules_ack_version = models.CharField(max_length=32, blank=True)
    rules_ack_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(eligibility__in=["PENDING", "APPROVED", "REJECTED"]),
                name="user_eligibility_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(locale__in=["th", "en"]),
                name="user_locale_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["eligibility"], name="user_eligibility_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.username} ({self.display_name})"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def institutional_id(self) -> str:
        """The institutional login ID (stored in ``username``)."""
        return self.username

    @property
    def display_name(self) -> str:
        if self.locale == self.Locale.EN and self.name_en:
            return self.name_en
        return self.name_th or self.name_en or self.username

    @property
    def display_name_en(self) -> str:
        return self.name_en or self.name_th or self.username

    @property
    def email_is_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_approved(self) -> bool:
        return self.eligibility == self.Eligibility.APPROVED and self.is_active

    @property
    def can_book(self) -> bool:
        """Account-state part of bookability; suspension is checked separately."""
        return self.is_authenticated and self.is_active and self.is_approved and self.email_is_verified
