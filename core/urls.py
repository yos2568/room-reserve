"""Application URL map (V3 section 9).

Included under a language prefix from ``roomreserve.urls``, so every route is
available in Thai and English.
"""

from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.public.grid, name="home"),
    path("availability/", views.public.availability_fragment, name="availability"),
    path("week/", views.public.week, name="week"),
    path("choose-room/", views.public.room_board, name="room_board"),
    path("lobby/", views.public.lobby, name="lobby"),
    path("rooms/<int:room_id>/", views.public.room_profile, name="room_profile"),
    path("r/<int:room_id>/", views.public.room_qr_landing, name="room_qr"),
    # Self-service check-in lives only behind the printed door QR (D-37): the
    # emailed link and the My bookings button no longer check anyone in.
    path(
        "r/<int:room_id>/bookings/<int:pk>/check-in/",
        views.public.room_check_in,
        name="room_check_in",
    ),
    # Identity -----------------------------------------------------------------
    path("register/", views.identity.register, name="register"),
    path("register/done/", views.identity.register_done, name="register_done"),
    path("verify/<str:token>/", views.identity.verify_email, name="verify_email"),
    path("invite/<str:token>/", views.identity.accept_invitation, name="accept_invitation"),
    path("login/", views.identity.login_view, name="login"),
    path("logout/", views.identity.logout_view, name="logout"),
    path("password-reset/", views.identity.password_reset, name="password_reset"),
    path("password-reset/done/", views.identity.password_reset_done, name="password_reset_done"),
    path("reset/<str:token>/", views.identity.password_reset_confirm, name="password_reset_confirm"),
    path("eligibility/", views.identity.eligibility_status, name="eligibility_status"),
    # Student booking ----------------------------------------------------------
    path("book/<int:room_id>/<str:slot>/", views.booking.book_slot, name="book_slot"),
    path(
        "book/<int:room_id>/<str:slot>/confirm/",
        views.booking.confirm_booking,
        name="confirm_booking",
    ),
    path("use-now/<int:room_id>/<str:slot>/", views.booking.use_now, name="use_now"),
    path("my-bookings/", views.booking.my_bookings, name="my_bookings"),
    path("my-bookings/<int:pk>/calendar.ics", views.booking.booking_ics, name="booking_ics"),
    path("my-bookings/<int:pk>/cancel/", views.booking.cancel_booking, name="cancel_booking"),
    path("my-bookings/series/<int:pk>/cancel/", views.booking.cancel_recurring, name="cancel_recurring"),
    path("my-bookings/<int:pk>/edit/", views.booking.edit_booking, name="edit_booking"),
    path("my-bookings/<int:pk>/move/", views.booking.move_booking, name="move_booking"),
    # Staff operations ---------------------------------------------------------
    path("staff/today/", views.staff.today, name="staff_today"),
    path("staff/room-admin/", views.staff.room_admin_dashboard, name="room_admin"),
    path(
        "staff/room-admin/bookings/<int:pk>/decision/",
        views.staff.decide_booking_approval,
        name="staff_decide_booking_approval",
    ),
    path("staff/approvals/<int:pk>/", views.staff.decide_eligibility, name="staff_decide_eligibility"),
    path(
        "staff/bookings/<int:pk>/check-in/",
        views.staff.assisted_check_in,
        name="staff_assisted_check_in",
    ),
    path("staff/bookings/<int:pk>/cancel/", views.staff.cancel_booking, name="staff_cancel_booking"),
    path("staff/bookings/<int:pk>/complete/", views.staff.complete_early, name="staff_complete_early"),
    path("staff/bookings/<int:pk>/review/", views.staff.resolve_review, name="staff_resolve_review"),
    path("staff/users/", views.staff.users, name="staff_users"),
    path(
        "staff/users/<int:pk>/declared-category/",
        views.staff.set_declared_category,
        name="staff_set_declared_category",
    ),
    path("staff/users/<int:pk>/suspend/", views.staff.manual_suspension, name="staff_suspend"),
    path("staff/users/<int:pk>/deactivate/", views.staff.deactivate_account, name="staff_deactivate"),
    path("staff/sanctions/<int:pk>/lift/", views.staff.lift_suspension, name="staff_lift"),
    path("staff/sanctions/<int:pk>/adjust/", views.staff.adjust_suspension, name="staff_adjust_suspension"),
    path(
        "staff/users/<int:pk>/violation/",
        views.staff.record_violation,
        name="staff_record_violation",
    ),
    path(
        "staff/violations/<int:pk>/void/",
        views.staff.void_violation,
        name="staff_void_violation",
    ),
    path("staff/closures/", views.staff.closures, name="staff_closures"),
    path("staff/closures/create/", views.staff.create_closure, name="staff_create_closure"),
    path("staff/closures/<int:pk>/revoke/", views.staff.revoke_closure, name="staff_revoke_closure"),
    path("staff/calendar/", views.staff.calendar, name="staff_calendar"),
    path("staff/calendar/set/", views.staff.set_override, name="staff_set_override"),
    path("staff/incidents/", views.staff.incidents, name="staff_incidents"),
    path("staff/incidents/create/", views.staff.create_incident, name="staff_create_incident"),
    path(
        "staff/incidents/<int:pk>/void/",
        views.staff.void_incident_violations,
        name="staff_void_incident",
    ),
    path("staff/roster/", views.staff.roster, name="staff_roster"),
    path(
        "staff/roster/<int:pk>/email/",
        views.staff.correct_roster_email,
        name="staff_correct_roster_email",
    ),
    path("staff/roster/import/", views.staff.import_roster, name="staff_import_roster"),
    path("staff/roster/export/", views.staff.roster_export, name="staff_roster_export"),
    path("staff/invitations/", views.staff.invitations, name="staff_invitations"),
    path("staff/invitations/create/", views.staff.create_invitation, name="staff_create_invitation"),
    path("staff/audit/", views.staff.audit, name="staff_audit"),
    path("staff/outbox/", views.staff.outbox_page, name="staff_outbox"),
    path("staff/outbox/drain/", views.staff.drain_outbox, name="staff_drain_outbox"),
    path("staff/stats/", views.staff.stats_page, name="staff_stats"),
    path("staff/stats/export/", views.staff.stats, name="staff_stats_export"),
    path("staff/posters/", views.staff.posters, name="staff_posters"),
    path("staff/posters/sheet/", views.staff.poster_sheet, name="staff_poster_sheet"),
    path("staff/policy/", views.staff.policy_page, name="staff_policy"),
    path(
        "staff/policy/create/",
        views.staff.create_policy_version,
        name="staff_create_policy_version",
    ),
    path(
        "staff/rooms/<int:pk>/audience/",
        views.staff.set_room_audience,
        name="staff_set_room_audience",
    ),
    path("staff/rooms/<int:pk>/profile/", views.staff.update_room_profile, name="staff_update_room_profile"),
    path(
        "staff/rooms/<int:pk>/administrator/",
        views.staff.set_room_administrator,
        name="staff_set_room_administrator",
    ),
    path("staff/rooms/<int:pk>/deactivate/", views.staff.deactivate_room, name="staff_deactivate_room"),
    path("staff/quota/", views.staff.quota_lookup, name="staff_quota_lookup"),
    path("staff/admin/", views.staff.admin_home, name="staff_admin"),
    # Content ------------------------------------------------------------------
    path("rules/", views.content.rules, name="rules"),
    path("help/", views.content.help_page, name="help"),
    path("complaints/", views.complaints.submit, name="complaint"),
    path("privacy/", views.content.privacy, name="privacy"),
]
