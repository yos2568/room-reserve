"""Identity views: registration, verification, invitation, sign-in and reset."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import Invitation
from core.services import identity as identity_service
from core.services import ratelimit
from core.services.eligibility import active_suspension
from core.services.errors import Code, OperationRejected
from core.services.policy import current_policy

from ._helpers import now, safe_next_url


@require_http_methods(["GET", "POST"])
def register(request):
    """Self-registration: ID + institutional email + name."""
    if request.user.is_authenticated:
        return redirect("core:my_bookings")

    context = {"operation_key": None, "outcome": None}
    if request.method == "POST":
        institutional_id = (request.POST.get("institutional_id") or "").strip()
        email = (request.POST.get("email") or "").strip()
        name = (request.POST.get("name") or "").strip()

        try:
            ratelimit.guard_registration(request)
            identity_service.start_registration(
                institutional_id=institutional_id, email=email, name=name
            )
        except OperationRejected as exc:
            outcome = exc.outcome
            # Never reveal whether a field matched the roster or an account: show
            # the same generic wording for a collision and a domain problem.
            generic = {
                Code.DUPLICATE_ACCOUNT,
                Code.DOMAIN_NOT_ALLOWED,
                Code.ROSTER_MISMATCH,
            }
            if outcome.code in generic:
                messages.error(
                    request,
                    "หากข้อมูลนี้ลงทะเบียนได้ เราได้ส่งลิงก์ยืนยันทางอีเมลแล้ว "
                    "ถ้ามีบัญชีอยู่แล้ว กรุณาเข้าสู่ระบบหรือรีเซ็ตรหัสผ่าน / "
                    "If these details can be registered, a confirmation email has been sent. "
                    "If you already have an account, sign in or reset your password.",
                )
            else:
                messages.error(request, outcome.message)
            context["outcome"] = outcome
            context["form"] = {"institutional_id": institutional_id, "email": email, "name": name}
            return render(request, "core/register.html", context, status=400)

        return redirect("core:register_done")

    return render(request, "core/register.html", context)


@require_GET
def register_done(request):
    return render(request, "core/register_done.html")


@require_http_methods(["GET", "POST"])
def verify_email(request, token: str):
    """Redeem a verification link, then let the student choose a password."""
    if request.method == "GET":
        invitation = identity_service.find_invitation(token, Invitation.Purpose.EMAIL_VERIFICATION)
        try:
            identity_service.assert_redeemable(invitation, Invitation.Purpose.EMAIL_VERIFICATION)
        except OperationRejected as exc:
            return render(
                request,
                "core/token_problem.html",
                {"outcome": exc.outcome, "purpose": "verification"},
                status=410,
            )
        return render(
            request,
            "core/set_password.html",
            {
                "token": token,
                "purpose": Invitation.Purpose.EMAIL_VERIFICATION,
                "account": invitation.user,
                "policy": current_policy(),
            },
        )

    # POST: redeem the token and set the password in one step.
    password = request.POST.get("password") or ""
    confirm = request.POST.get("password_confirm") or ""
    if password != confirm:
        messages.error(request, "รหัสผ่านทั้งสองช่องไม่ตรงกัน / The two passwords do not match.")
        return render(
            request,
            "core/set_password.html",
            {
                "token": token,
                "purpose": Invitation.Purpose.EMAIL_VERIFICATION,
                "policy": current_policy(),
            },
            status=400,
        )

    try:
        user, _ = identity_service.verify_email(token)
        identity_service.set_initial_password(
            user=user,
            password=password,
            raw_token=token,
            purpose=Invitation.Purpose.EMAIL_VERIFICATION,
        )
    except OperationRejected as exc:
        return render(
            request,
            "core/token_problem.html",
            {"outcome": exc.outcome, "purpose": "verification"},
            status=410,
        )

    messages.success(
        request,
        "ยืนยันอีเมลเรียบร้อย กรุณาเข้าสู่ระบบ / Email verified. Please sign in.",
    )
    return redirect("core:login")


@require_http_methods(["GET", "POST"])
def accept_invitation(request, token: str):
    """Redeem a staff-issued activation link."""
    invitation = identity_service.find_invitation(token, Invitation.Purpose.ACCOUNT_ACTIVATION)

    if request.method == "GET":
        try:
            identity_service.assert_redeemable(invitation, Invitation.Purpose.ACCOUNT_ACTIVATION)
        except OperationRejected as exc:
            return render(
                request,
                "core/token_problem.html",
                {"outcome": exc.outcome, "purpose": "invitation"},
                status=410,
            )
        return render(
            request,
            "core/set_password.html",
            {
                "token": token,
                "purpose": Invitation.Purpose.ACCOUNT_ACTIVATION,
                "account": invitation.user,
                "policy": current_policy(),
            },
        )

    password = request.POST.get("password") or ""
    if password != (request.POST.get("password_confirm") or ""):
        messages.error(request, "รหัสผ่านทั้งสองช่องไม่ตรงกัน / The two passwords do not match.")
        return render(
            request,
            "core/set_password.html",
            {
                "token": token,
                "purpose": Invitation.Purpose.ACCOUNT_ACTIVATION,
                "policy": current_policy(),
            },
            status=400,
        )

    try:
        invitation = identity_service.assert_redeemable(invitation, Invitation.Purpose.ACCOUNT_ACTIVATION)
        identity_service.set_initial_password(
            user=invitation.user,
            password=password,
            raw_token=token,
            purpose=Invitation.Purpose.ACCOUNT_ACTIVATION,
        )
    except OperationRejected as exc:
        return render(
            request,
            "core/token_problem.html",
            {"outcome": exc.outcome, "purpose": "invitation"},
            status=410,
        )

    messages.success(request, "ตั้งรหัสผ่านเรียบร้อย กรุณาเข้าสู่ระบบ / Password set. Please sign in.")
    return redirect("core:login")


@require_http_methods(["GET", "POST"])
def login_view(request):
    from django.contrib.auth import authenticate

    if request.user.is_authenticated:
        return redirect("core:my_bookings")

    if request.method == "POST":
        institutional_id = (request.POST.get("institutional_id") or "").strip()
        password = request.POST.get("password") or ""

        try:
            ratelimit.guard(request, identifier=institutional_id)
        except OperationRejected as exc:
            messages.error(request, exc.outcome.message)
            return render(request, "core/login.html", {}, status=429)

        user = authenticate(request, username=institutional_id, password=password)
        if user is None:
            # One message for both a missing account and a wrong password.
            messages.error(
                request,
                "รหัสนิสิตหรือรหัสผ่านไม่ถูกต้อง / Incorrect institutional ID or password.",
            )
            return render(
                request,
                "core/login.html",
                {"institutional_id": institutional_id},
                status=401,
            )

        auth_login(request, user)
        return redirect(safe_next_url(request, request.POST.get("next"), "core:my_bookings"))

    return render(request, "core/login.html", {"next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("core:home")


@login_required
@require_GET
def eligibility_status(request):
    moment = now()
    return render(
        request,
        "core/eligibility_status.html",
        {
            "suspension": active_suspension(request.user, moment),
            "moment": moment,
            "policy": current_policy(),
        },
    )


# --- Password reset ------------------------------------------------------------
# A reset link is a transactional message, so it is enqueued on the outbox like
# every other notification rather than sent inline from the view (V3 section 10).
# The response is generic in both directions so the form never reveals whether an
# address is registered.


@require_http_methods(["GET", "POST"])
def password_reset(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        try:
            ratelimit.guard_registration(request)
        except OperationRejected:
            # Same generic outcome: a limited address learns nothing extra.
            return redirect("core:password_reset_done")

        identity_service.start_password_reset(email)
        return redirect("core:password_reset_done")

    return render(request, "core/password_reset.html")


@require_GET
def password_reset_done(request):
    return render(request, "core/password_reset_done.html")


@require_http_methods(["GET", "POST"])
def password_reset_confirm(request, token: str):
    invitation = identity_service.find_invitation(token, Invitation.Purpose.PASSWORD_RESET)

    if request.method == "GET":
        try:
            identity_service.assert_redeemable(invitation, Invitation.Purpose.PASSWORD_RESET)
        except OperationRejected as exc:
            return render(
                request,
                "core/token_problem.html",
                {"outcome": exc.outcome, "purpose": "reset"},
                status=410,
            )
        return render(
            request,
            "core/password_reset_confirm.html",
            {"token": token, "account": invitation.user},
        )

    password = request.POST.get("password") or ""
    if password != (request.POST.get("password_confirm") or ""):
        messages.error(request, "รหัสผ่านทั้งสองช่องไม่ตรงกัน / The two passwords do not match.")
        return render(
            request,
            "core/password_reset_confirm.html",
            {"token": token},
            status=400,
        )

    try:
        invitation = identity_service.assert_redeemable(invitation, Invitation.Purpose.PASSWORD_RESET)
        identity_service.set_initial_password(
            user=invitation.user,
            password=password,
            raw_token=token,
            purpose=Invitation.Purpose.PASSWORD_RESET,
        )
    except OperationRejected as exc:
        return render(
            request,
            "core/token_problem.html",
            {"outcome": exc.outcome, "purpose": "reset"},
            status=410,
        )

    messages.success(request, "ตั้งรหัสผ่านใหม่เรียบร้อย กรุณาเข้าสู่ระบบ / Password updated. Please sign in.")
    return redirect("core:login")
