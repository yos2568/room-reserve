"""Identity: registration, roster matching, invitations and token replay (A20).

V3 section 8 separates three facts that are easy to conflate: owning an email
address, being on the department roster, and being approved to book. Verification
proves the first, the roster match proves the second, and staff approval covers
everyone else. The tests below also pin the privacy rule that a public response
never reveals roster membership, and the single-use rule for tokens.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse

from core.models import Booking, EligibleStudent, Invitation, Notification, User
from core.services import clock, identity, slots
from core.services.eligibility import account_block_code
from core.services.errors import Code, OperationRejected
from tests import factories, helpers

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()


def target(hour: int = 11):
    return slots.slot_start_for(DAY, hour)


def register(institutional_id: str, email: str, name: str = "ทดสอบ ระบบ"):
    return identity.start_registration(institutional_id=institutional_id, email=email, name=name)


def assert_rejected(exc_info, code):
    outcome = exc_info.value.outcome
    assert outcome.code == code, outcome.code


# --- Registration --------------------------------------------------------------


def test_self_registration_creates_a_pending_account_with_no_usable_password(db):
    result = register("66001000001", "somchai@student.chula.ac.th", "สมชาย ทดสอบ")

    assert result.created is True
    user = User.objects.get(username="66001000001")
    assert user.eligibility == User.Eligibility.PENDING
    assert user.email_is_verified is False
    assert user.has_usable_password() is False, "an unverified account must not be signable"
    assert user.is_active is True
    assert account_block_code(user) == Code.EMAIL_UNVERIFIED


def test_registration_response_is_identical_for_roster_and_non_roster_details(db):
    """A public caller cannot tell whether the details are on the roster."""
    on_roster = factories.make_roster_entry(institutional_id="66001000002", email="a@student.chula.ac.th")
    assert on_roster.is_active

    listed = register("66001000002", "a@student.chula.ac.th")
    unlisted = register("66001000003", "b@student.chula.ac.th")

    assert listed.created == unlisted.created is True
    a = User.objects.get(username="66001000002")
    b = User.objects.get(username="66001000003")
    # Both are PENDING and both still require the emailed link: the roster match
    # is applied at verification, not disclosed at registration.
    assert a.eligibility == b.eligibility == User.Eligibility.PENDING
    assert a.email_is_verified is False and b.email_is_verified is False
    assert Invitation.objects.filter(user=a).count() == Invitation.objects.filter(user=b).count() == 1


def test_environment_default_policy_comes_from_the_institution_domain(db):
    assert identity.email_domain_allowed("student@student.chula.ac.th") is True
    assert identity.email_domain_allowed("student@gmail.com") is False
    assert identity.email_domain_allowed("student@STUDENT.CHULA.AC.TH") is True

    with pytest.raises(OperationRejected) as exc:
        register("66001000004", "someone@gmail.com")
    assert_rejected(exc, Code.DOMAIN_NOT_ALLOWED)


def test_duplicate_institutional_id_is_refused(db):
    factories.make_user(username="66001000005", email="first@student.chula.ac.th")

    with pytest.raises(OperationRejected) as exc:
        register("66001000005", "second@student.chula.ac.th")
    assert_rejected(exc, Code.DUPLICATE_ACCOUNT)


def test_duplicate_email_is_refused(db):
    factories.make_user(username="66001000006", email="taken@student.chula.ac.th")

    with pytest.raises(OperationRejected) as exc:
        register("66001000007", "taken@student.chula.ac.th")
    assert_rejected(exc, Code.DUPLICATE_ACCOUNT)


def test_a_collision_does_not_rebind_the_existing_account(db):
    existing = factories.make_user(username="66001000008", email="owner@student.chula.ac.th")

    with pytest.raises(OperationRejected):
        register("66001000008", "other@student.chula.ac.th")
    with pytest.raises(OperationRejected):
        register("66001000009", "owner@student.chula.ac.th")

    existing.refresh_from_db()
    assert existing.email == "owner@student.chula.ac.th"


# --- Verification and eligibility ----------------------------------------------


def test_verification_without_a_roster_match_stays_pending_and_cannot_book(frozen, rooms):
    result = register("66001000010", "unlisted@student.chula.ac.th")
    user, _ = identity.verify_email(result.raw_token)

    assert user.email_is_verified is True, "verification proves the address"
    assert user.eligibility == User.Eligibility.PENDING, "it does not prove department membership"

    outcome = helpers.advance_booking(user, rooms[0], target(11))
    assert not outcome.ok
    assert outcome.code == Code.PENDING_APPROVAL


def test_an_exact_active_roster_match_auto_approves_and_allows_booking(frozen, rooms):
    entry = factories.make_roster_entry(
        institutional_id="66001000011", email="listed@student.chula.ac.th", instrument="คลาริเน็ต"
    )

    result = register("66001000011", "listed@student.chula.ac.th")
    user, _ = identity.verify_email(result.raw_token)

    assert user.eligibility == User.Eligibility.APPROVED
    assert user.eligibility_decided_at is not None
    assert user.email_is_verified is True

    entry.refresh_from_db()
    assert entry.account_id == user.pk, "the roster entry links to the account it vouched for"

    outcome = helpers.advance_booking(user, rooms[0], target(11))
    assert outcome.ok, outcome.code


def test_an_inactive_roster_entry_does_not_auto_approve(frozen, rooms):
    factories.make_roster_entry(
        institutional_id="66001000012", email="left@student.chula.ac.th", is_active=False
    )

    result = register("66001000012", "left@student.chula.ac.th")
    user, _ = identity.verify_email(result.raw_token)

    assert user.eligibility == User.Eligibility.PENDING
    assert not helpers.advance_booking(user, rooms[0], target(11)).ok


def test_a_roster_id_with_a_different_email_does_not_auto_approve(frozen, rooms):
    """The ID and the approved address must match together."""
    factories.make_roster_entry(institutional_id="66001000013", email="approved@student.chula.ac.th")

    result = register("66001000013", "different@student.chula.ac.th")
    user, _ = identity.verify_email(result.raw_token)

    assert user.eligibility == User.Eligibility.PENDING


def test_unverified_and_inactive_accounts_cannot_book(frozen, rooms):
    unverified = factories.make_user(verified=False)
    inactive = factories.make_user(is_active=False)

    assert account_block_code(unverified) == Code.EMAIL_UNVERIFIED
    assert account_block_code(inactive) == Code.INACTIVE_ACCOUNT

    first = helpers.advance_booking(unverified, rooms[0], target(11))
    assert not first.ok and first.code == Code.EMAIL_UNVERIFIED

    second = helpers.advance_booking(inactive, rooms[1], target(11))
    assert not second.ok and second.code == Code.INACTIVE_ACCOUNT


def test_staff_approval_admits_a_student_who_is_not_on_the_roster(frozen, staff_user, rooms):
    result = register("66001000014", "appeal@student.chula.ac.th")
    user, _ = identity.verify_email(result.raw_token)
    assert user.eligibility == User.Eligibility.PENDING

    with pytest.raises(OperationRejected) as exc:
        identity.decide_eligibility(
            user=user, decision=User.Eligibility.APPROVED, actor=staff_user, reason="  "
        )
    assert_rejected(exc, Code.INVALID_INPUT)

    identity.decide_eligibility(
        user=user,
        decision=User.Eligibility.APPROVED,
        actor=staff_user,
        reason="Verified with the department office.",
    )
    user.refresh_from_db()
    assert user.eligibility == User.Eligibility.APPROVED
    assert user.eligibility_decided_by_id == staff_user.pk
    assert helpers.advance_booking(user, rooms[0], target(11)).ok


def test_rejection_blocks_booking(frozen, staff_user, rooms):
    result = register("66001000015", "rejected@student.chula.ac.th")
    user, _ = identity.verify_email(result.raw_token)

    identity.decide_eligibility(
        user=user, decision=User.Eligibility.REJECTED, actor=staff_user, reason="Not enrolled."
    )
    user.refresh_from_db()
    outcome = helpers.advance_booking(user, rooms[0], target(11))
    assert not outcome.ok
    assert outcome.code == Code.NOT_ELIGIBLE


def test_changing_email_invalidates_verification_and_approval(frozen, rooms):
    user = factories.make_user(username="66001000016", email="old@student.chula.ac.th")
    assert user.eligibility == User.Eligibility.APPROVED

    _, raw_token = identity.change_email(user=user, new_email="new@student.chula.ac.th")
    user.refresh_from_db()

    assert user.email == "new@student.chula.ac.th"
    assert user.email_verified_at is None
    assert user.eligibility == User.Eligibility.PENDING

    outcome = helpers.advance_booking(user, rooms[0], target(11))
    assert not outcome.ok and outcome.code == Code.EMAIL_UNVERIFIED

    identity.verify_email(raw_token)
    user.refresh_from_db()
    assert user.email_is_verified is True
    assert user.eligibility == User.Eligibility.PENDING, "the roster must vouch again"


def test_changing_to_an_already_registered_address_is_refused(db):
    other = factories.make_user(username="66001000017", email="busy@student.chula.ac.th")
    user = factories.make_user(username="66001000018", email="mine@student.chula.ac.th")

    with pytest.raises(OperationRejected) as exc:
        identity.change_email(user=user, new_email=other.email)
    assert_rejected(exc, Code.DUPLICATE_ACCOUNT)


# --- Tokens: single use, generation and expiry ---------------------------------


def test_a_used_verification_token_cannot_be_replayed(db):
    result = register("66001000019", "once@student.chula.ac.th")
    identity.verify_email(result.raw_token)

    with pytest.raises(OperationRejected) as exc:
        identity.verify_email(result.raw_token)
    assert_rejected(exc, Code.TOKEN_USED)


def test_resending_verification_invalidates_the_previous_link(db):
    result = register("66001000020", "resend@student.chula.ac.th")
    user = User.objects.get(username="66001000020")

    _, fresh_token = identity.resend_verification(user)

    with pytest.raises(OperationRejected) as exc:
        identity.verify_email(result.raw_token)
    assert_rejected(exc, Code.TOKEN_EXPIRED)

    verified, _ = identity.verify_email(fresh_token)
    assert verified.pk == user.pk

    # A verified account has nothing left to resend.
    with pytest.raises(OperationRejected) as exc:
        identity.resend_verification(user)
    assert_rejected(exc, Code.INVALID_INPUT)


def test_an_expired_verification_token_is_rejected(db):
    moment = factories.bangkok(2026, 9, 14, 9, 0)
    with clock.frozen_clock(moment):
        result = register("66001000021", "slow@student.chula.ac.th")

    just_inside = moment + identity.VERIFICATION_TTL - timedelta(minutes=1)
    with clock.frozen_clock(just_inside):
        assert identity.invitation_is_current(result.invitation)

    just_past = moment + identity.VERIFICATION_TTL + timedelta(seconds=1)
    with clock.frozen_clock(just_past):
        with pytest.raises(OperationRejected) as exc:
            identity.verify_email(result.raw_token)
        assert_rejected(exc, Code.TOKEN_EXPIRED)


def test_only_the_token_digest_is_persisted(db):
    result = register("66001000022", "private@student.chula.ac.th")

    invitation = result.invitation
    assert invitation.token_digest != result.raw_token
    assert invitation.token_digest == identity.token_digest(result.raw_token)
    assert len(invitation.token_digest) == 64

    # The plaintext travels in the outbox payload, which is why the outbox is
    # staff-only; it is never written to the invitation row or the audit trail.
    notification = Notification.objects.get(kind="email_verification", recipient=invitation.user)
    assert notification.payload["token"] == result.raw_token


def test_unknown_and_used_tokens_are_rejected_cleanly(db):
    with pytest.raises(OperationRejected) as exc:
        identity.verify_email("not-a-real-token")
    assert_rejected(exc, Code.INVALID_TOKEN)

    with pytest.raises(OperationRejected) as exc:
        identity.verify_email("")
    assert_rejected(exc, Code.INVALID_TOKEN)


# --- Staff invitations ---------------------------------------------------------


def test_staff_invitation_creates_an_approved_account_that_still_needs_activation(db):
    staff = factories.make_user(is_operational_staff=True)
    user, invitation, raw_token = identity.invite_account(
        institutional_id="66001000023",
        email="invited@student.chula.ac.th",
        name="ผู้รับเชิญ ทดสอบ",
        actor=staff,
        staff=False,
        reason="New first-year student.",
    )

    assert user.eligibility == User.Eligibility.APPROVED
    assert user.email_verified_at is not None, "the department already knows this address"
    assert user.has_usable_password() is False, "no default password is ever issued"
    assert invitation.purpose == Invitation.Purpose.ACCOUNT_ACTIVATION

    identity.set_initial_password(
        user=user,
        password="a-long-enough-password-1",
        raw_token=raw_token,
        purpose=Invitation.Purpose.ACCOUNT_ACTIVATION,
    )
    user.refresh_from_db()
    assert user.check_password("a-long-enough-password-1")


def test_weak_passwords_are_refused_at_activation(db):
    staff = factories.make_user(is_operational_staff=True)
    user, _, raw_token = identity.invite_account(
        institutional_id="66001000024",
        email="weak@student.chula.ac.th",
        name="ทดสอบ",
        actor=staff,
        staff=False,
    )

    with pytest.raises(OperationRejected) as exc:
        identity.set_initial_password(
            user=user,
            password="12345678",
            raw_token=raw_token,
            purpose=Invitation.Purpose.ACCOUNT_ACTIVATION,
        )
    assert_rejected(exc, Code.INVALID_INPUT)


def test_invitation_cannot_rebind_an_existing_identity(db):
    staff = factories.make_user(is_operational_staff=True)
    existing = factories.make_user(username="66001000025", email="known@student.chula.ac.th")

    with pytest.raises(OperationRejected) as exc:
        identity.invite_account(
            institutional_id="66001000025",
            email="elsewhere@student.chula.ac.th",
            name="ทดสอบ",
            actor=staff,
            staff=False,
        )
    assert_rejected(exc, Code.DUPLICATE_ACCOUNT)

    with pytest.raises(OperationRejected) as exc:
        identity.invite_account(
            institutional_id="66001000026",
            email=existing.email,
            name="ทดสอบ",
            actor=staff,
            staff=False,
        )
    assert_rejected(exc, Code.DUPLICATE_ACCOUNT)


def test_activating_with_another_accounts_token_is_refused(db):
    staff = factories.make_user(is_operational_staff=True)
    first, _, token_one = identity.invite_account(
        institutional_id="66001000027",
        email="one@student.chula.ac.th",
        name="หนึ่ง",
        actor=staff,
        staff=False,
    )
    second, _, _ = identity.invite_account(
        institutional_id="66001000028",
        email="two@student.chula.ac.th",
        name="สอง",
        actor=staff,
        staff=False,
    )

    with pytest.raises(OperationRejected) as exc:
        identity.set_initial_password(
            user=second,
            password="a-long-enough-password-2",
            raw_token=token_one,
            purpose=Invitation.Purpose.ACCOUNT_ACTIVATION,
        )
    assert_rejected(exc, Code.INVALID_TOKEN)

    first.refresh_from_db()
    assert first.has_usable_password() is False


# --- Password reset ------------------------------------------------------------


def test_password_reset_is_generic_and_enqueues_only_for_real_accounts(db):
    user = factories.make_user(username="66001000029", email="reset@student.chula.ac.th")

    # An unknown address produces no error and no work.
    identity.start_password_reset("nobody@student.chula.ac.th")
    assert not Invitation.objects.filter(purpose=Invitation.Purpose.PASSWORD_RESET).exists()

    identity.start_password_reset(user.email)
    invitation = Invitation.objects.get(purpose=Invitation.Purpose.PASSWORD_RESET)
    assert invitation.user_id == user.pk

    notification = Notification.objects.get(kind="password_reset", recipient=user)
    assert notification.payload["invitation_id"] == invitation.pk
    assert notification.status == Notification.Status.PENDING


def test_password_reset_token_cannot_be_used_as_a_verification_token(db):
    user = factories.make_user(
        username="66001000030", email="mixed@student.chula.ac.th", eligibility=User.Eligibility.PENDING
    )
    _, raw_token = identity.create_invitation(
        user=user,
        purpose=Invitation.Purpose.PASSWORD_RESET,
        ttl=identity.PASSWORD_RESET_TTL,
    )

    with pytest.raises(OperationRejected) as exc:
        identity.verify_email(raw_token)
    assert_rejected(exc, Code.INVALID_TOKEN)


# --- Roster bookkeeping --------------------------------------------------------


def test_roster_entries_and_accounts_stay_distinct(frozen, rooms):
    """A roster row without an account is not a user, and vice versa."""
    factories.make_roster_entry(institutional_id="66001000031", email="pending@student.chula.ac.th")
    assert EligibleStudent.objects.count() == 1
    assert User.objects.count() == 0

    user = factories.make_user(username="66001000032", email="nocase@student.chula.ac.th")
    assert EligibleStudent.objects.filter(account=user).count() == 0
    assert Booking.objects.count() == 0


# --- Completing self-registration ----------------------------------------------
# The verification link is single use, so redeeming it and setting the password
# have to be ONE operation. Split across two service calls, the first consumed the
# link and the second refused it, and every registration through the real route
# ended on "that link has already been used" (HTTP 410) with no usable account.

GOOD_PASSWORD = "a-long-enough-password-1"


def test_complete_registration_verifies_the_address_and_sets_the_password(frozen):
    entry = factories.make_roster_entry(institutional_id="66001000040", email="onestep@student.chula.ac.th")
    result = register("66001000040", "onestep@student.chula.ac.th")

    user = identity.complete_registration(raw_token=result.raw_token, password=GOOD_PASSWORD)

    assert user.pk == result.invitation.user_id
    assert user.email_is_verified is True
    assert user.check_password(GOOD_PASSWORD)
    assert user.eligibility == User.Eligibility.APPROVED
    entry.refresh_from_db()
    assert entry.account_id == user.pk


def test_complete_registration_consumes_the_link_exactly_once(db):
    result = register("66001000041", "onceonly@student.chula.ac.th")
    identity.complete_registration(raw_token=result.raw_token, password=GOOD_PASSWORD)

    result.invitation.refresh_from_db()
    assert result.invitation.used_at is not None

    with pytest.raises(OperationRejected) as exc:
        identity.complete_registration(raw_token=result.raw_token, password="another-long-password-2")
    assert_rejected(exc, Code.TOKEN_USED)


def test_a_rejected_password_does_not_consume_the_link(db):
    """A weak password is a form problem, not a reason to need a new link."""
    result = register("66001000042", "retry@student.chula.ac.th")

    with pytest.raises(OperationRejected) as exc:
        identity.complete_registration(raw_token=result.raw_token, password="12345678")
    assert_rejected(exc, Code.INVALID_INPUT)
    assert exc.value.outcome.data["errors"], "the validator messages travel with the rejection"

    result.invitation.refresh_from_db()
    assert result.invitation.used_at is None, "the link must survive a bad password"

    user = identity.complete_registration(raw_token=result.raw_token, password=GOOD_PASSWORD)
    assert user.check_password(GOOD_PASSWORD)


def test_the_verification_route_completes_registration(frozen, client):
    """The route, not the service: this is what a student actually uses."""
    factories.make_roster_entry(institutional_id="66001000043", email="route@student.chula.ac.th")
    result = register("66001000043", "route@student.chula.ac.th")

    response = client.post(
        reverse("core:verify_email", args=[result.raw_token]),
        {"password": GOOD_PASSWORD, "password_confirm": GOOD_PASSWORD},
    )

    assert response.status_code == 302, "a completed registration redirects to sign in"
    assert response["Location"] == reverse("core:login")

    user = User.objects.get(username="66001000043")
    assert user.email_is_verified is True
    assert user.check_password(GOOD_PASSWORD)
    assert user.eligibility == User.Eligibility.APPROVED


def test_the_verification_route_reports_a_weak_password_and_keeps_the_link(frozen, client):
    result = register("66001000044", "weak@student.chula.ac.th")
    url = reverse("core:verify_email", args=[result.raw_token])

    response = client.post(url, {"password": "12345678", "password_confirm": "12345678"})

    assert response.status_code == 400, "a weak password is a form error, not a dead link"

    result.invitation.refresh_from_db()
    assert result.invitation.used_at is None

    retry = client.post(url, {"password": GOOD_PASSWORD, "password_confirm": GOOD_PASSWORD})
    assert retry.status_code == 302
    assert User.objects.get(username="66001000044").check_password(GOOD_PASSWORD)


def test_the_verification_route_refuses_a_mismatched_confirmation(frozen, client):
    result = register("66001000045", "mismatch@student.chula.ac.th")

    response = client.post(
        reverse("core:verify_email", args=[result.raw_token]),
        {"password": GOOD_PASSWORD, "password_confirm": "a-different-password-2"},
    )

    assert response.status_code == 400
    result.invitation.refresh_from_db()
    assert result.invitation.used_at is None
    assert User.objects.get(username="66001000045").has_usable_password() is False
