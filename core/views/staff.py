"""Staff operations (V3 section 8).

Ten named staff accounts share identical operational permissions. Nothing here
can grant superuser, change secrets or bypass a booking rule, and every mutation
routes through a service under the shared lock with a recorded reason.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models import (
    AuditEvent,
    Booking,
    CalendarOverride,
    Closure,
    EligibleStudent,
    InstrumentCategory,
    JobHeartbeat,
    Notification,
    Room,
    ServiceIncident,
    Suspension,
    User,
    Violation,
)
from core.services import identity as identity_service
from core.services import incidents as incident_service
from core.services import maintenance, outbox, sanctions
from core.services import roster as roster_service
from core.services.cancel import cancel as cancel_service
from core.services.checkin import check_in as check_in_service
from core.services.csvio import write_csv
from core.services.errors import OperationOutcome
from core.services.policy import current_policy
from core.services.quota import quota_used

from ._helpers import add_outcome_message, now, operation_key, run_view_operation, staff_required


def _success(**data) -> OperationOutcome:
    return OperationOutcome.success(**data)


def _parse_dt(value: str) -> datetime | None:
    """Parse a datetime-local input into an aware Bangkok datetime."""
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timezone.is_naive(naive):
        from zoneinfo import ZoneInfo

        naive = timezone.make_aware(naive, ZoneInfo("Asia/Bangkok"))
    return naive


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- Today ---------------------------------------------------------------------


@staff_required
@require_GET
def today(request):
    moment = now()
    local_date = timezone.localdate(moment)

    bookings = (
        Booking.objects.filter(slot_date=local_date)
        .select_related("room", "user")
        .order_by("slot_start", "room__position")
    )

    pending = User.objects.filter(eligibility=User.Eligibility.PENDING, is_active=True).order_by(
        "date_joined"
    )
    active_suspensions = maintenance.active_suspensions(moment)
    pending_reviews = maintenance.reviews_pending()
    failed_mail = Notification.objects.filter(status=Notification.Status.EXHAUSTED).order_by("-created_at")[
        :20
    ]

    heartbeat = JobHeartbeat.objects.filter(name="tick").first()
    from django.conf import settings as dj_settings

    heartbeat_stale = (
        heartbeat is None
        or (moment - heartbeat.last_run_at).total_seconds() > dj_settings.SCHEDULER_HEARTBEAT_STALE_SECONDS
    )

    oldest = outbox.oldest_actionable(moment)
    outbox_age = (moment - oldest.next_attempt_at).total_seconds() if oldest else 0

    return render(
        request,
        "core/staff/today.html",
        {
            "moment": moment,
            "local_date": local_date,
            "bookings": bookings,
            "pending": pending,
            "active_suspensions": active_suspensions,
            "pending_reviews": pending_reviews,
            "failed_mail": failed_mail,
            "open_incidents": incident_service.open_incidents(),
            "sanctions_under_review": incident_service.sanctions_under_review(),
            "heartbeat": heartbeat,
            "heartbeat_stale": heartbeat_stale,
            "outbox_age_seconds": int(outbox_age),
            "outbox_stale": int(outbox_age) > dj_settings.OUTBOX_AGE_WARN_SECONDS,
            "policy": current_policy(),
            "operation_key": str(uuid.uuid4()),
        },
    )


@staff_required
@require_POST
def decide_eligibility(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    decision = request.POST.get("decision") or ""
    reason = request.POST.get("reason") or ""

    outcome = run_view_operation(
        request=request,
        operation="staff_decide_eligibility",
        payload={"user": pk, "decision": decision, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(
            user_id=identity_service.decide_eligibility(
                user=user, decision=decision, actor=ctx.actor, reason=reason
            ).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_today")


@staff_required
@require_POST
def assisted_check_in(request, pk: int):
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    if not reason:
        messages.error(
            request,
            "ต้องระบุเหตุผลและยืนยันการมาใช้งานจริง / A reason is required and attendance must be verified.",
        )
        return redirect(request.POST.get("next") or "core:staff_today")

    outcome = run_view_operation(
        request=request,
        operation="staff_assisted_check_in",
        payload={"booking": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(
            **check_in_service(ctx, booking=booking, room=booking.room, staff_assisted=True, reason=reason)
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_today")


@staff_required
@require_POST
def cancel_booking(request, pk: int):
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "ต้องระบุเหตุผล / A reason is required.")
        return redirect(request.POST.get("next") or "core:staff_today")

    outcome = run_view_operation(
        request=request,
        operation="staff_cancel_booking",
        payload={"booking": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(**cancel_service(ctx, booking=booking, reason=reason, staff_action=True)),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_today")


@staff_required
@require_POST
def complete_early(request, pk: int):
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_complete_early",
        payload={"booking": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(
            booking_id=maintenance.complete_session_early(ctx, booking=booking, reason=reason).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_today")


@staff_required
@require_POST
def resolve_review(request, pk: int):
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk)
    note = (request.POST.get("note") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_resolve_review",
        payload={"booking": pk, "note": note},
        key=operation_key(request),
        body=lambda ctx: _success(booking_id=maintenance.resolve_review(ctx, booking=booking, note=note).pk),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_today")


# --- Users, violations and sanctions ------------------------------------------


@staff_required
@require_GET
def users(request):
    moment = now()
    query = (request.GET.get("q") or "").strip()
    queryset = User.objects.all().order_by("username")
    if query:
        queryset = queryset.filter(
            Q(username__icontains=query) | Q(email__icontains=query) | Q(name_th__icontains=query)
        )
    queryset = list(queryset[:100])
    suspensions = {s.user_id: s for s in Suspension.objects.active(moment).select_related("user")}

    # Open strikes per user, loaded in one query. Staff need to see the strikes
    # behind a sanction in order to act on an appeal: the student is told to
    # contact the office, so the office has to have the control.
    strikes: dict[int, list[Violation]] = {}
    for violation in Violation.objects.filter(
        user_id__in=[user.pk for user in queryset], voided_at__isnull=True
    ).order_by("-occurred_at"):
        strikes.setdefault(violation.user_id, []).append(violation)

    rows = [
        {
            "user": user,
            "suspension": suspensions.get(user.pk),
            "violations": strikes.get(user.pk, []),
        }
        for user in queryset
    ]

    return render(
        request,
        "core/staff/users.html",
        {
            "rows": rows,
            "query": query,
            "moment": moment,
            "policy": current_policy(),
            "operation_key": uuid.uuid4(),
        },
    )


@staff_required
@require_POST
def record_violation(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    note = (request.POST.get("note") or "").strip()
    counts_as_strike = request.POST.get("counts_as_strike") == "on"

    outcome = run_view_operation(
        request=request,
        operation="staff_record_violation",
        payload={"user": pk, "note": note, "counts_as_strike": counts_as_strike},
        key=operation_key(request),
        body=lambda ctx: _success(
            violation_id=sanctions.record_manual_violation(
                ctx, user=user, note=note, counts_as_strike=counts_as_strike
            ).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_users")


@staff_required
@require_POST
def void_violation(request, pk: int):
    violation = get_object_or_404(Violation, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_void_violation",
        payload={"violation": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(**sanctions.void_violation(ctx, violation=violation, reason=reason)),
    )
    if outcome.ok and outcome.data.get("review_required"):
        messages.warning(
            request,
            "การยกเลิกคะแนนนี้เปิดให้ทบทวนมาตรการลงโทษที่เกี่ยวข้อง / "
            "Voiding this strike opened a review of the linked sanction.",
        )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_users")


@staff_required
@require_POST
def manual_suspension(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    ends_at = _parse_dt(request.POST.get("ends_at") or "")

    outcome = run_view_operation(
        request=request,
        operation="staff_manual_suspension",
        payload={"user": pk, "reason": reason, "ends_at": request.POST.get("ends_at") or ""},
        key=operation_key(request),
        body=lambda ctx: _success(
            suspension_id=sanctions.apply_manual_suspension(ctx, user=user, reason=reason, ends_at=ends_at).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_users")


@staff_required
@require_POST
def adjust_suspension(request, pk: int):
    suspension = get_object_or_404(Suspension, pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    ends_at = _parse_dt(request.POST.get("ends_at") or "")

    outcome = run_view_operation(
        request=request,
        operation="staff_adjust_suspension",
        payload={"suspension": pk, "reason": reason, "ends_at": request.POST.get("ends_at") or ""},
        key=operation_key(request),
        body=lambda ctx: _success(
            suspension_id=sanctions.extend_or_shorten_suspension(
                ctx, suspension=suspension, ends_at=ends_at, reason=reason, staff_override=True
            ).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_users")


@staff_required
@require_POST
def lift_suspension(request, pk: int):
    suspension = get_object_or_404(Suspension, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_lift_suspension",
        payload={"suspension": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(
            suspension_id=sanctions.lift_suspension(ctx, suspension=suspension, reason=reason).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_users")


@staff_required
@require_POST
def deactivate_account(request, pk: int):
    user = get_object_or_404(User, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_deactivate_account",
        payload={"user": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(**maintenance.deactivate_account(ctx, user=user, reason=reason)),
    )
    add_outcome_message(request, outcome)
    return redirect(request.POST.get("next") or "core:staff_users")


# --- Closures ------------------------------------------------------------------


@staff_required
@require_GET
def closures(request):
    moment = now()
    upcoming = Closure.objects.filter(ends_at__gte=moment).select_related("room", "created_by")[:50]
    past = Closure.objects.filter(ends_at__lt=moment).select_related("room")[:20]

    preview = None
    room_id = _int_or_none(request.GET.get("room"))
    starts_raw = request.GET.get("starts_at")
    ends_raw = request.GET.get("ends_at")
    if starts_raw and ends_raw:
        starts_at = _parse_dt(starts_raw)
        ends_at = _parse_dt(ends_raw)
        if starts_at and ends_at:
            from core.models import Room as RoomModel

            room = RoomModel.objects.filter(pk=room_id).first() if room_id else None
            try:
                preview = maintenance.preview_closure(room, starts_at, ends_at)
            except Exception:
                messages.error(request, "ช่วงเวลาที่เลือกไม่ถูกต้อง / The selected time range is not valid.")

    return render(
        request,
        "core/staff/closures.html",
        {
            "upcoming": upcoming,
            "past": past,
            "preview": preview,
            "rooms": Room.objects.all(),
            "selected_room": room_id,
            "starts_raw": starts_raw or "",
            "ends_raw": ends_raw or "",
            "moment": moment,
            "operation_key": str(uuid.uuid4()),
        },
    )


@staff_required
@require_POST
def create_closure(request):
    room_id = _int_or_none(request.POST.get("room"))
    room = Room.objects.filter(pk=room_id).first() if room_id else None
    starts_at = _parse_dt(request.POST.get("starts_at") or "")
    ends_at = _parse_dt(request.POST.get("ends_at") or "")
    reason = (request.POST.get("reason") or "").strip()
    acknowledged = request.POST.get("acknowledge_in_use") == "on"

    if not (starts_at and ends_at):
        messages.error(request, "ต้องระบุช่วงเวลา / A time range is required.")
        return redirect("core:staff_closures")

    outcome = run_view_operation(
        request=request,
        operation="staff_create_closure",
        payload={
            "room": room_id,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "reason": reason,
            "acknowledged": acknowledged,
        },
        key=operation_key(request),
        body=lambda ctx: _success(
            closure_id=maintenance.create_closure(
                ctx,
                room=room,
                starts_at=starts_at,
                ends_at=ends_at,
                reason=reason,
                acknowledged_in_use=acknowledged,
            ).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_closures")


@staff_required
@require_POST
def revoke_closure(request, pk: int):
    closure = get_object_or_404(Closure, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_revoke_closure",
        payload={"closure": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(
            closure_id=maintenance.revoke_closure(ctx, closure=closure, reason=reason).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_closures")


# --- Calendar ------------------------------------------------------------------


@staff_required
@require_GET
def calendar(request):
    moment = now()
    overrides = CalendarOverride.objects.all()[:60]
    return render(
        request,
        "core/staff/calendar.html",
        {
            "overrides": overrides,
            "rooms": Room.objects.all(),
            "moment": moment,
            "policy": current_policy(),
            "operation_key": str(uuid.uuid4()),
        },
    )


@staff_required
@require_POST
def set_override(request):
    raw_date = (request.POST.get("local_date") or "").strip()
    try:
        local_date = date.fromisoformat(raw_date)
    except ValueError:
        messages.error(request, "วันที่ไม่ถูกต้อง / That date is not valid.")
        return redirect("core:staff_calendar")

    is_open = request.POST.get("is_open") == "on"
    open_hour = _int_or_none(request.POST.get("open_hour"))
    close_hour = _int_or_none(request.POST.get("close_hour"))
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_set_override",
        payload={
            "local_date": raw_date,
            "is_open": is_open,
            "open_hour": open_hour,
            "close_hour": close_hour,
            "reason": reason,
        },
        key=operation_key(request),
        body=lambda ctx: _success(
            override_id=maintenance.set_calendar_override(
                ctx,
                local_date=local_date,
                is_open=is_open,
                open_hour=open_hour,
                close_hour=close_hour,
                reason=reason,
            ).pk
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_calendar")


# --- Incidents -----------------------------------------------------------------


@staff_required
@require_GET
def incidents(request):
    moment = now()
    return render(
        request,
        "core/staff/incidents.html",
        {
            "incidents": ServiceIncident.objects.all().prefetch_related("rooms")[:50],
            "rooms": Room.objects.all(),
            "moment": moment,
            "operation_key": str(uuid.uuid4()),
        },
    )


@staff_required
@require_POST
def create_incident(request):
    starts_at = _parse_dt(request.POST.get("starts_at") or "")
    ends_at = _parse_dt(request.POST.get("ends_at") or "")
    reason = (request.POST.get("reason") or "").strip()
    room_ids = [_int_or_none(value) for value in request.POST.getlist("rooms")]
    room_ids = [value for value in room_ids if value is not None]
    rooms = list(Room.objects.filter(pk__in=room_ids))
    cancel_scheduled = request.POST.get("cancel_scheduled") == "on"

    if not (starts_at and ends_at):
        messages.error(request, "ต้องระบุช่วงเวลา / A time range is required.")
        return redirect("core:staff_incidents")

    outcome = run_view_operation(
        request=request,
        operation="staff_create_incident",
        payload={
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "rooms": sorted(room_ids),
            "reason": reason,
            "cancel_scheduled": cancel_scheduled,
        },
        key=operation_key(request),
        body=lambda ctx: _success(
            **incident_service.create_incident(
                ctx,
                starts_at=starts_at,
                ends_at=ends_at,
                rooms=rooms,
                reason=reason,
                cancel_scheduled=cancel_scheduled,
            )
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_incidents")


@staff_required
@require_POST
def void_incident_violations(request, pk: int):
    incident = get_object_or_404(ServiceIncident, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_void_incident_violations",
        payload={"incident": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(
            **incident_service.void_incident_violations(ctx, incident=incident, reason=reason)
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_incidents")


# --- Roster --------------------------------------------------------------------


@staff_required
@require_GET
def roster(request):
    return render(
        request,
        "core/staff/roster.html",
        {
            "entries": EligibleStudent.objects.all()[:200],
            "total": EligibleStudent.objects.count(),
            "active": EligibleStudent.objects.filter(is_active=True).count(),
            "report": None,
            "moment": now(),
        },
    )


@staff_required
@require_POST
def import_roster(request):
    upload = request.FILES.get("file")
    dry_run = request.POST.get("dry_run") == "on"
    allow_email_change = request.POST.get("allow_email_change") == "on"
    batch = (request.POST.get("batch") or f"upload-{timezone.now():%Y%m%d%H%M%S}").strip()

    if upload is None:
        messages.error(request, "กรุณาเลือกไฟล์ CSV / Please choose a CSV file.")
        return redirect("core:staff_roster")

    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp874", errors="replace")

    try:
        report = roster_service.import_roster_text(
            text=text,
            actor=request.user,
            dry_run=dry_run,
            batch=batch,
            allow_email_change=allow_email_change,
        )
    except ValueError as exc:
        # All-or-nothing: the transaction was rolled back, so report the conflict.
        report = roster_service.ImportReport(rows_read=0, errors=[roster_service.RowError(0, str(exc))])

    messages.info(request, report.summary())
    return render(
        request,
        "core/staff/roster.html",
        {
            "entries": EligibleStudent.objects.all()[:200],
            "total": EligibleStudent.objects.count(),
            "active": EligibleStudent.objects.filter(is_active=True).count(),
            "report": report,
            "moment": now(),
        },
    )


@staff_required
@require_GET
def roster_export(request):
    rows = (
        EligibleStudent.objects.all()
        .order_by("institutional_id")
        .values_list(
            "institutional_id",
            "email",
            "name_th",
            "name_en",
            "program",
            "instrument",
            "instrument_category",
            "year",
            "is_active",
        )
    )
    body = write_csv(
        [
            "institutional_id",
            "email",
            "name_th",
            "name_en",
            "program",
            "instrument",
            "instrument_category",
            "year",
            "is_active",
        ],
        rows,
    )
    response = HttpResponse(body, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="roster.csv"'
    return response


# --- Invitations ---------------------------------------------------------------


@staff_required
@require_GET
def invitations(request):
    from core.models import Invitation

    return render(
        request,
        "core/staff/invitations.html",
        {
            "invitations": Invitation.objects.select_related("user", "created_by")[:100],
            "moment": now(),
            "issued": request.session.pop("issued_invitation", None),
        },
    )


@staff_required
@require_POST
def create_invitation(request):
    institutional_id = (request.POST.get("institutional_id") or "").strip()
    email = (request.POST.get("email") or "").strip()
    name = (request.POST.get("name") or "").strip()
    staff = request.POST.get("staff") == "on"

    outcome = run_view_operation(
        request=request,
        operation="staff_create_invitation",
        payload={"institutional_id": institutional_id, "email": email, "staff": staff},
        key=operation_key(request),
        body=lambda ctx: _invite(ctx, institutional_id, email, name, staff),
    )

    if outcome.ok:
        # The link is shown once, in this response only, and never logged.
        request.session["issued_invitation"] = {
            "institutional_id": institutional_id,
            "link": outcome.data["link"],
        }
    add_outcome_message(request, outcome)
    return redirect("core:staff_invitations")


def _invite(ctx, institutional_id, email, name, staff):
    user, invitation, raw_token = identity_service.invite_account(
        institutional_id=institutional_id, email=email, name=name, actor=ctx.actor, staff=staff
    )
    from django.urls import reverse

    link = reverse("core:accept_invitation", kwargs={"token": raw_token})
    return _success(user_id=user.pk, invitation_id=invitation.pk, link=link)


# --- Audit, outbox, stats, posters, policy ------------------------------------


@staff_required
@require_GET
def audit(request):
    queryset = AuditEvent.objects.select_related("actor").order_by("-occurred_at")
    action = (request.GET.get("action") or "").strip()
    entity = (request.GET.get("entity") or "").strip()
    if action:
        queryset = queryset.filter(action__icontains=action)
    if entity:
        queryset = queryset.filter(entity_type__icontains=entity)
    return render(
        request,
        "core/staff/audit.html",
        {"events": queryset[:200], "action": action, "entity": entity, "moment": now()},
    )


@staff_required
@require_GET
def outbox_page(request):
    queryset = Notification.objects.select_related("recipient").order_by("-created_at")
    status = (request.GET.get("status") or "").strip()
    if status:
        queryset = queryset.filter(status=status)
    moment = now()
    oldest = outbox.oldest_actionable(moment)
    from django.conf import settings as dj_settings

    outbox_age = int((moment - oldest.next_attempt_at).total_seconds()) if oldest else 0

    return render(
        request,
        "core/staff/outbox.html",
        {
            "notifications": queryset[:200],
            "status": status,
            "counts": dict(Notification.objects.values_list("status").annotate(total=Count("id"))),
            "oldest": oldest,
            "outbox_age_seconds": outbox_age,
            "outbox_stale": outbox_age > dj_settings.OUTBOX_AGE_WARN_SECONDS,
            "moment": moment,
        },
    )


@staff_required
@require_POST
def drain_outbox(request):
    from core.services.notifications import drain

    result = drain(now())
    messages.info(
        request,
        f"claimed={result['claimed']} sent={result['sent']} failed={result['failed']} stale={result['stale']}",
    )
    return redirect("core:staff_outbox")


@staff_required
@require_GET
def stats(request):
    """CSV statistics export. Formula-safe because exports are user-controlled data."""
    moment = now()
    start = moment - timedelta(days=30)

    rows = []
    for room in Room.objects.all().order_by("position"):
        bookings = Booking.objects.filter(room=room, slot_start__gte=start)
        rows.append(
            [
                room.number,
                str(room),
                bookings.count(),
                bookings.filter(status=Booking.Status.COMPLETED).count(),
                bookings.filter(status=Booking.Status.NO_SHOW).count(),
                bookings.filter(status=Booking.Status.CANCELLED).count(),
                bookings.filter(status=Booking.Status.IN_USE).count(),
            ]
        )

    body = write_csv(
        ["room_number", "room_label", "bookings_30d", "completed", "no_show", "cancelled", "in_use"],
        rows,
    )
    response = HttpResponse(body, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="room-stats.csv"'
    return response


@staff_required
@require_GET
def stats_page(request):
    moment = now()
    return render(
        request,
        "core/staff/stats.html",
        {
            "rooms": Room.objects.all().order_by("position"),
            "moment": moment,
            "since": moment - timedelta(days=30),
        },
    )


@staff_required
@require_GET
def posters(request):
    rooms = Room.objects.filter(is_active=True).order_by("position")
    return render(
        request,
        "core/staff/posters.html",
        {"rooms": rooms, "moment": now(), "policy": current_policy()},
    )


@staff_required
@require_GET
def poster_sheet(request):
    """One printable A4 poster per room, with the short QR URL and contact."""
    import base64

    from core.services.posters import qr_png_bytes

    rooms = Room.objects.filter(is_active=True).order_by("position")
    cards = []
    for room in rooms:
        target = request.build_absolute_uri(f"/r/{room.pk}/")
        cards.append(
            {
                "room": room,
                "target": target,
                "qr": base64.b64encode(qr_png_bytes(target)).decode("ascii"),
            }
        )
    return render(
        request,
        "core/staff/poster_sheet.html",
        {"cards": cards, "moment": now(), "policy": current_policy()},
    )


@staff_required
@require_GET
def policy_page(request):
    moment = now()
    from core.models import PolicyVersion

    return render(
        request,
        "core/staff/policy.html",
        {
            "current": current_policy(),
            "versions": PolicyVersion.objects.all()[:20],
            "moment": moment,
            "operation_key": str(uuid.uuid4()),
        },
    )


@staff_required
@require_POST
def create_policy_version(request):
    snapshot = {
        "horizon_days": _int_or_none(request.POST.get("horizon_days")),
        "daily_quota": _int_or_none(request.POST.get("daily_quota")),
        "checkin_grace_minutes": _int_or_none(request.POST.get("checkin_grace_minutes")),
        "strike_window_days": _int_or_none(request.POST.get("strike_window_days")),
        "strike_threshold": _int_or_none(request.POST.get("strike_threshold")),
        "auto_suspension_days": _int_or_none(request.POST.get("auto_suspension_days")),
        "reminder_lead_minutes": _int_or_none(request.POST.get("reminder_lead_minutes")),
        "institution_email_domain": (request.POST.get("institution_email_domain") or "").strip(),
    }
    snapshot = {key: value for key, value in snapshot.items() if value not in (None, "")}
    note = (request.POST.get("note") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_create_policy_version",
        payload=snapshot,
        key=operation_key(request),
        body=lambda ctx: _success(
            version=maintenance.update_policy(ctx, snapshot=snapshot, label="Staff edit", note=note).version
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_policy")


@staff_required
@require_GET
def admin_home(request):
    """Permission-limited configuration overview.

    Raw lifecycle, deadline, ownership and sanction editing is not offered here:
    every operational change goes through a service (V3 section 2).
    """
    moment = now()
    return render(
        request,
        "core/staff/admin.html",
        {
            "rooms": Room.objects.all().order_by("position").prefetch_related("allowed_categories"),
            "instrument_categories": InstrumentCategory.choices,
            "policy": current_policy(),
            "moment": moment,
            "is_maintainer": request.user.is_superuser,
            "counts": {
                "users": User.objects.count(),
                "bookings": Booking.objects.count(),
                "violations": Violation.objects.count(),
                "suspensions": Suspension.objects.count(),
            },
            "operation_key": str(uuid.uuid4()),
        },
    )


@staff_required
@require_POST
def deactivate_room(request, pk: int):
    room = get_object_or_404(Room, pk=pk)
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_deactivate_room",
        payload={"room": pk, "reason": reason},
        key=operation_key(request),
        body=lambda ctx: _success(**maintenance.deactivate_room(ctx, room=room, reason=reason)),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_admin")


@staff_required
@require_POST
def set_room_audience(request, pk: int):
    """Change who may reserve a room, without a developer running a command.

    The department has already changed the room layout once, so the audience is a
    configuration a staff member has to be able to adjust. The service that applies
    it is the same one the seed command uses, so the two cannot drift.
    """
    room = get_object_or_404(Room, pk=pk)
    scope = (request.POST.get("reservation_scope") or "").strip().upper()
    categories = request.POST.getlist("categories")

    if scope not in Room.ReservationScope.values:
        messages.error(request, "ต้องระบุขอบเขตการจอง / A reservation scope is required.")
        return redirect("core:staff_admin")

    outcome = run_view_operation(
        request=request,
        operation="staff_set_room_audience",
        payload={"room": pk, "reservation_scope": scope, "categories": sorted(categories)},
        key=operation_key(request),
        body=lambda ctx: _success(
            **maintenance.set_room_audience(ctx, room=room, scope=scope, categories=categories)
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:staff_admin")


@staff_required
@require_POST
def correct_roster_email(request, pk: int):
    """Correct one roster entry's address, with a mandatory reason.

    The import deliberately refuses to change an address on an existing entry, so
    without this there is no way to remedy a roster that is simply wrong — and the
    department's list does hold addresses a student cannot register with.
    """
    entry = get_object_or_404(EligibleStudent, pk=pk)
    email = (request.POST.get("email") or "").strip()
    reason = (request.POST.get("reason") or "").strip()

    outcome = run_view_operation(
        request=request,
        operation="staff_correct_roster_email",
        payload={"entry": pk, "email": email},
        key=operation_key(request),
        body=lambda ctx: _success(
            **roster_service.correct_email(ctx, entry=entry, email=email, reason=reason)
        ),
    )

    if (
        outcome.ok
        and outcome.data.get("linked_account_id")
        and not outcome.data.get("linked_account_matches")
    ):
        messages.warning(
            request,
            "ที่อยู่นี้ไม่ตรงกับบัญชีที่เชื่อมอยู่แล้ว การอนุมัติเดิมยังคงอยู่ "
            "/ The linked account's address no longer matches this entry. Its existing "
            "approval is unchanged; review the account if it should be re-checked.",
        )

    add_outcome_message(request, outcome)
    return redirect("core:staff_roster")


@staff_required
@require_GET
def quota_lookup(request):
    """Small read-only helper used by the staff user page."""
    user_id = _int_or_none(request.GET.get("user"))
    if user_id is None:
        return HttpResponseBadRequest("user is required")
    user = get_object_or_404(User, pk=user_id)
    moment = now()
    from core.services.availability import my_upcoming

    return render(
        request,
        "core/staff/quota_lookup.html",
        {
            "target": user,
            "used": quota_used(user, timezone.localdate(moment)),
            "upcoming": my_upcoming(user, moment),
            "moment": moment,
            "policy": current_policy(),
        },
    )
