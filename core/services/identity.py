"""Identity: self-registration, email verification, invitations and approval.

V3 section 8 flow: institutional ID + institutional email + name → expiring
verification → password → eligibility. Email ownership alone does not establish
department membership, so verification and eligibility are separate steps.

Two privacy rules are enforced here:

* Public responses never reveal roster membership, so a mismatch and a
  non-roster address produce the same message.
* A duplicate or contradictory ID/email submission cannot take over an existing
  account, and the error does not confirm which field collided.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import EligibleStudent, Invitation, User

from . import clock
from .audit import record_audit
from .errors import Code, OperationRejected
from .notifications import KIND_EMAIL_VERIFICATION, KIND_INVITATION, KIND_PASSWORD_RESET
from .outbox import enqueue
from .policy import current_policy

logger = logging.getLogger(__name__)

VERIFICATION_TTL = timedelta(days=3)
ACTIVATION_TTL = timedelta(days=14)
PASSWORD_RESET_TTL = timedelta(hours=2)

TOKEN_BYTES = 32


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def token_digest(raw_token: str) -> str:
    """Only the digest is stored; the plaintext token is never logged."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def email_domain_allowed(email: str) -> bool:
    domain = normalize_email(email).rpartition("@")[2]
    expected = current_policy().snapshot.get("institution_email_domain") or settings.INSTITUTION_EMAIL_DOMAIN
    return domain == expected.lower()


# --- Invitations ---------------------------------------------------------------


def create_invitation(*, user, purpose: str, ttl: timedelta, actor=None) -> tuple[Invitation, str]:
    """Issue a single-use token, superseding any earlier one for this purpose.

    Re-sending increments the generation, so an older link stops working.
    """
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    digest = token_digest(raw_token)

    with transaction.atomic():
        previous = Invitation.objects.select_for_update().filter(user=user, purpose=purpose)
        latest_generation = previous.order_by("-generation").values_list("generation", flat=True).first()
        generation = (latest_generation or 0) + 1

        previous.filter(used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now())

        invitation = Invitation.objects.create(
            user=user,
            token_digest=digest,
            generation=generation,
            purpose=purpose,
            expires_at=clock.now() + ttl,
            created_by=actor if getattr(actor, "pk", None) else None,
        )

    record_audit(
        action="invitation.created",
        entity_type="Invitation",
        entity_id=invitation.pk,
        actor=actor,
        changes={"purpose": purpose, "generation": generation, "expires_at": invitation.expires_at},
        # The token itself is deliberately absent from the audit trail.
    )
    return invitation, raw_token


def find_invitation(raw_token: str, purpose: str | None = None) -> Invitation | None:
    if not raw_token:
        return None
    query = Invitation.objects.filter(token_digest=token_digest(raw_token)).select_related("user")
    if purpose:
        query = query.filter(purpose=purpose)
    return query.first()


def invitation_is_current(invitation: Invitation) -> bool:
    """False when a newer generation has been issued for the same purpose."""
    latest = (
        Invitation.objects.filter(user_id=invitation.user_id, purpose=invitation.purpose)
        .order_by("-generation")
        .values_list("generation", flat=True)
        .first()
    )
    return latest == invitation.generation


def assert_redeemable(invitation: Invitation | None, purpose: str | None = None) -> Invitation:
    if invitation is None:
        raise OperationRejected(Code.INVALID_TOKEN)
    if purpose and invitation.purpose != purpose:
        raise OperationRejected(Code.INVALID_TOKEN)
    if not invitation_is_current(invitation):
        raise OperationRejected(Code.TOKEN_EXPIRED)
    if invitation.revoked_at is not None:
        raise OperationRejected(Code.TOKEN_EXPIRED)
    if invitation.used_at is not None:
        raise OperationRejected(Code.TOKEN_USED)
    if invitation.expires_at <= clock.now():
        raise OperationRejected(Code.TOKEN_EXPIRED)
    return invitation


# --- Registration --------------------------------------------------------------


@dataclass
class RegistrationResult:
    created: bool
    invitation: Invitation | None = None
    raw_token: str | None = None


def start_registration(*, institutional_id: str, email: str, name: str, actor=None) -> RegistrationResult:
    """Create a pending account and send a verification link.

    The account has no usable password until the emailed token is redeemed, so an
    unverified registration cannot be signed into.
    """
    institutional_id = (institutional_id or "").strip()
    email = normalize_email(email)
    name = (name or "").strip()

    if not institutional_id or not email or not name:
        raise OperationRejected(Code.INVALID_INPUT)
    if not email_domain_allowed(email):
        raise OperationRejected(Code.DOMAIN_NOT_ALLOWED)

    with transaction.atomic():
        # Neither branch says which field collided: that would confirm account or
        # roster membership to an unauthenticated caller.
        if User.objects.filter(username=institutional_id).exists():
            raise OperationRejected(Code.DUPLICATE_ACCOUNT)
        if User.objects.filter(email__iexact=email).exists():
            raise OperationRejected(Code.DUPLICATE_ACCOUNT)

        try:
            user = User.objects.create_user(
                username=institutional_id,
                email=email,
                password=None,
                name_th=name,
                eligibility=User.Eligibility.PENDING,
                is_active=True,
            )
        except IntegrityError as exc:
            raise OperationRejected(Code.DUPLICATE_ACCOUNT) from exc

        user.set_unusable_password()
        user.save(update_fields=["password"])

    invitation, raw_token = create_invitation(
        user=user, purpose=Invitation.Purpose.EMAIL_VERIFICATION, ttl=VERIFICATION_TTL, actor=actor
    )

    record_audit(
        action="account.registered",
        entity_type="User",
        entity_id=user.pk,
        actor=None,
        actor_label="self-registration",
        changes={"institutional_id": institutional_id, "eligibility": user.eligibility},
    )

    enqueue(
        kind=KIND_EMAIL_VERIFICATION,
        recipient=user,
        dedupe_key=f"email_verification:{invitation.pk}",
        payload={
            "invitation_id": invitation.pk,
            "token": raw_token,
            "institutional_id": institutional_id,
        },
    )
    return RegistrationResult(created=True, invitation=invitation, raw_token=raw_token)


def resend_verification(user, actor=None) -> tuple[Invitation, str]:
    """Issue a fresh verification link, invalidating the previous generation.

    The persisted verification timestamp is the authority, not the caller's
    in-memory copy: a caller holding a stale object must not be able to issue a
    new link for an account that is already verified.
    """
    if User.objects.filter(pk=user.pk, email_verified_at__isnull=False).exists():
        raise OperationRejected(Code.INVALID_INPUT)
    invitation, raw_token = create_invitation(
        user=user, purpose=Invitation.Purpose.EMAIL_VERIFICATION, ttl=VERIFICATION_TTL, actor=actor
    )
    enqueue(
        kind=KIND_EMAIL_VERIFICATION,
        recipient=user,
        dedupe_key=f"email_verification:{invitation.pk}",
        payload={"invitation_id": invitation.pk, "token": raw_token},
    )
    return invitation, raw_token


def verify_email(raw_token: str) -> tuple[User, Invitation]:
    """Redeem a verification token: mark the email verified, then assess eligibility."""
    invitation = assert_redeemable(find_invitation(raw_token, Invitation.Purpose.EMAIL_VERIFICATION))
    user = invitation.user

    invitation.used_at = clock.now()
    invitation.save(update_fields=["used_at"])

    user.email_verified_at = clock.now()
    user.save(update_fields=["email_verified_at"])

    record_audit(
        action="account.email_verified",
        entity_type="User",
        entity_id=user.pk,
        actor=user,
        changes={"email": user.email},
    )

    apply_roster_eligibility(user)
    return user, invitation


def apply_roster_eligibility(user) -> str:
    """Auto-approve an exact active roster match; otherwise leave PENDING.

    Verifies the institutional ID and the approved email *together*, because an
    email address alone does not establish department membership.
    """
    match = EligibleStudent.objects.filter(
        institutional_id=user.institutional_id,
        email__iexact=user.email,
        is_active=True,
    ).first()

    if match is None:
        if user.eligibility == User.Eligibility.APPROVED:
            # A previously approved account keeps its approval; a roster change
            # does not silently revoke a staff decision.
            return user.eligibility
        return user.eligibility

    if match.account_id is None:
        match.account = user
        match.save(update_fields=["account", "updated_at"])

    user.eligibility = User.Eligibility.APPROVED
    user.eligibility_decided_at = clock.now()
    user.eligibility_reason = "Automatic match against the active department roster."
    user.save(update_fields=["eligibility", "eligibility_decided_at", "eligibility_reason"])

    record_audit(
        action="eligibility.approved",
        entity_type="User",
        entity_id=user.pk,
        actor=None,
        actor_label="roster-match",
        changes={"eligibility": user.eligibility},
        reason=user.eligibility_reason,
    )
    enqueue(
        kind="eligibility_decision",
        recipient=user,
        dedupe_key=f"eligibility_decision:{user.pk}:{user.eligibility_decided_at.isoformat()}",
        payload={"decision": user.eligibility, "reason": user.eligibility_reason},
    )
    return user.eligibility


# --- Password ------------------------------------------------------------------


def start_password_reset(email: str) -> None:
    """Enqueue a reset link for an active account.

    Always returns without indicating whether the address exists, so the endpoint
    cannot be used to enumerate registered students.
    """
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return

    for user in User.objects.filter(email__iexact=normalized, is_active=True):
        invitation, raw_token = create_invitation(
            user=user,
            purpose=Invitation.Purpose.PASSWORD_RESET,
            ttl=PASSWORD_RESET_TTL,
            actor=None,
        )
        enqueue(
            kind=KIND_PASSWORD_RESET,
            recipient=user,
            dedupe_key=f"password_reset:{invitation.pk}",
            payload={"invitation_id": invitation.pk, "token": raw_token},
        )
        record_audit(
            action="account.password_reset_requested",
            entity_type="User",
            entity_id=user.pk,
            actor=None,
            actor_label="self-service",
            changes={"invitation_generation": invitation.generation},
        )


def set_initial_password(*, user, password: str, raw_token: str, purpose: str) -> None:
    """Set the account password after a successful token redemption."""
    invitation = assert_redeemable(find_invitation(raw_token, purpose), purpose)

    if invitation.user_id != user.pk:
        raise OperationRejected(Code.INVALID_TOKEN)

    _store_password(user, password, via=purpose)

    invitation.used_at = clock.now()
    invitation.save(update_fields=["used_at"])


def complete_registration(*, raw_token: str, password: str) -> User:
    """Redeem a self-registration link and set the password, in one step.

    The verification link is single use, so it has to be consumed exactly once.
    Redeeming it and setting the password as two separate service calls consumed
    it twice: ``verify_email`` stamped ``used_at`` and then ``set_initial_password``
    rejected the very token it had just used, so every real registration ended on
    "that link has already been used". Service-level tests missed it because they
    drove the two halves independently.

    The password is validated *before* the link is consumed, so a password that
    fails the validators leaves the link usable and the student can try again.
    """
    purpose = Invitation.Purpose.EMAIL_VERIFICATION
    invitation = assert_redeemable(find_invitation(raw_token, purpose), purpose)
    user = invitation.user

    with transaction.atomic():
        _store_password(user, password, via=purpose)

        invitation.used_at = clock.now()
        invitation.save(update_fields=["used_at"])

        user.email_verified_at = clock.now()
        user.save(update_fields=["email_verified_at"])

        record_audit(
            action="account.email_verified",
            entity_type="User",
            entity_id=user.pk,
            actor=user,
            changes={"email": user.email},
        )
        apply_roster_eligibility(user)

    return user


def _store_password(user, password: str, *, via: str) -> None:
    """Validate and persist a new password, leaving every token untouched.

    Validation runs first on purpose: the caller may still have to consume a
    single-use link, and a rejected password must not consume it.
    """
    try:
        validate_password(password, user)
    except ValidationError as exc:
        raise OperationRejected(Code.INVALID_INPUT, errors=list(exc.messages)) from exc

    user.set_password(password)
    user.save(update_fields=["password"])

    record_audit(
        action="account.password_set",
        entity_type="User",
        entity_id=user.pk,
        actor=user,
        changes={"via": via},
    )


def change_email(*, user, new_email: str, actor=None) -> tuple[User, str]:
    """Change an address, which invalidates verification and the eligibility state.

    V3 section 8: "Changing email invalidates verification and requires a new
    eligibility check."
    """
    new_email = normalize_email(new_email)
    if not new_email or "@" not in new_email:
        raise OperationRejected(Code.INVALID_INPUT)
    if not email_domain_allowed(new_email):
        raise OperationRejected(Code.DOMAIN_NOT_ALLOWED)

    if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        raise OperationRejected(Code.DUPLICATE_ACCOUNT)

    previous = user.email
    user.email = new_email
    user.email_verified_at = None
    user.eligibility = User.Eligibility.PENDING
    user.eligibility_decided_at = None
    user.eligibility_reason = ""
    user.save(
        update_fields=[
            "email",
            "email_verified_at",
            "eligibility",
            "eligibility_decided_at",
            "eligibility_reason",
        ]
    )

    record_audit(
        action="account.email_changed",
        entity_type="User",
        entity_id=user.pk,
        actor=actor or user,
        changes={"from": previous, "to": new_email},
    )
    _, raw_token = resend_verification(user, actor=actor)
    return user, raw_token


# --- Staff decisions -----------------------------------------------------------


def decide_eligibility(*, user, decision: str, actor, reason: str) -> User:
    """Staff approval or rejection. A reason is mandatory."""
    if decision not in {User.Eligibility.APPROVED, User.Eligibility.REJECTED}:
        raise OperationRejected(Code.INVALID_INPUT)
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)

    user.eligibility = decision
    user.eligibility_decided_at = clock.now()
    user.eligibility_decided_by = actor
    user.eligibility_reason = reason.strip()
    user.save(
        update_fields=[
            "eligibility",
            "eligibility_decided_at",
            "eligibility_decided_by",
            "eligibility_reason",
        ]
    )

    record_audit(
        action=f"eligibility.{decision.lower()}",
        entity_type="User",
        entity_id=user.pk,
        actor=actor,
        changes={"eligibility": decision},
        reason=reason,
    )
    enqueue(
        kind="eligibility_decision",
        recipient=user,
        dedupe_key=f"eligibility_decision:{user.pk}:{user.eligibility_decided_at.isoformat()}",
        payload={"decision": decision, "reason": reason},
    )
    return user


def invite_account(*, institutional_id: str, email: str, name: str, actor, staff: bool, reason: str = ""):
    """Create an invited account and return its activation link.

    Staff identities are supplied by the owner; this never grants superuser, and
    it never resets a password on an account that already exists.
    """
    institutional_id = (institutional_id or "").strip()
    email = normalize_email(email)
    if not institutional_id or not email:
        raise OperationRejected(Code.INVALID_INPUT)

    with transaction.atomic():
        user = User.objects.filter(username=institutional_id).first()
        if user is not None:
            if user.email.lower() != email:
                # Refuse rather than silently rebind an existing identity.
                raise OperationRejected(Code.DUPLICATE_ACCOUNT)
            created = False
        else:
            if User.objects.filter(email__iexact=email).exists():
                raise OperationRejected(Code.DUPLICATE_ACCOUNT)
            user = User.objects.create_user(
                username=institutional_id,
                email=email,
                password=None,
                name_th=name,
                eligibility=User.Eligibility.APPROVED,
                is_operational_staff=staff,
                email_verified_at=clock.now(),
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            created = True

    invitation, raw_token = create_invitation(
        user=user,
        purpose=Invitation.Purpose.ACCOUNT_ACTIVATION,
        ttl=ACTIVATION_TTL,
        actor=actor,
    )
    record_audit(
        action="account.invited",
        entity_type="User",
        entity_id=user.pk,
        actor=actor,
        changes={"staff": staff, "created": created},
        reason=reason,
    )
    enqueue(
        kind=KIND_INVITATION,
        recipient=user,
        dedupe_key=f"invitation:{invitation.pk}",
        payload={"invitation_id": invitation.pk, "token": raw_token, "staff": staff},
    )
    return user, invitation, raw_token
