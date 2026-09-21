"""Authenticated complaint submission, delivered through the retrying outbox."""

import uuid

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.validators import validate_email
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.models import Notification, Room
from core.services import outbox, ratelimit
from core.services.notifications import KIND_COMPLAINT

SALT = "roomreserve.complaint"


class ComplaintForm(forms.Form):
    submission = forms.CharField(widget=forms.HiddenInput)
    subject = forms.CharField(
        label="หัวข้อ / Subject",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "field"}),
    )
    room = forms.ModelChoiceField(
        label="ห้องที่เกี่ยวข้อง (ถ้ามี) / Room (optional)",
        queryset=Room.objects.none(),
        required=False,
        empty_label="ไม่ระบุห้อง / No specific room",
        widget=forms.Select(attrs={"class": "field"}),
    )
    message = forms.CharField(
        label="รายละเอียด / Message",
        min_length=10,
        max_length=4000,
        help_text="ระบุวัน เวลา และปัญหาที่พบ / Include the date, time, and what happened.",
        widget=forms.Textarea(attrs={"class": "field", "rows": 7}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = Room.objects.filter(is_active=True).order_by("position", "number")


@login_required
@require_http_methods(["GET", "POST"])
def submit(request):
    token = signing.dumps({"user": request.user.pk, "reference": uuid.uuid4().hex}, salt=SALT)
    form = ComplaintForm(request.POST if request.method == "POST" else None, initial={"submission": token})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            data = signing.loads(form.cleaned_data["submission"], salt=SALT, max_age=86400)
            if data["user"] != request.user.pk:
                raise signing.BadSignature()
        except (signing.BadSignature, KeyError, TypeError):
            form.add_error(None, "แบบฟอร์มหมดอายุ กรุณาโหลดหน้าใหม่ / Reload the page and try again.")
            status = 400
        else:
            key = f"complaint:{request.user.pk}:{data['reference']}"
            if Notification.objects.filter(dedupe_key=key).exists():
                messages.success(request, "ได้รับข้อความนี้แล้ว / This message has already been received.")
                return redirect("core:complaint")
            if not ratelimit.hit("complaint", str(request.user.pk), limit=3, window=3600):
                form.add_error(None, "ส่งได้สูงสุด 3 ครั้งต่อชั่วโมง กรุณาลองใหม่ภายหลัง / Please try again in an hour.")
                status = 429
            else:
                validate_email(settings.COMPLAINT_RECIPIENT_EMAIL)
                validate_email(request.user.email)
                room = form.cleaned_data["room"]
                with transaction.atomic():
                    outbox.enqueue(
                        kind=KIND_COMPLAINT,
                        recipient=None,
                        recipient_email=settings.COMPLAINT_RECIPIENT_EMAIL,
                        dedupe_key=key,
                        language="th",
                        payload={
                            "reference": data["reference"],
                            "sender_name": request.user.display_name,
                            "reply_email": request.user.email,
                            "email_verified": request.user.email_is_verified,
                            "subject": form.cleaned_data["subject"],
                            "room": str(room) if room else "—",
                            "message": form.cleaned_data["message"],
                        },
                    )
                messages.success(
                    request,
                    f"รับเรื่องแล้วและรอส่งอีเมล / Queued for email delivery. เลขอ้างอิง: {data['reference']}",
                )
                return redirect("core:complaint")
    elif request.method == "POST":
        status = 400
    response = render(
        request,
        "core/complaint.html",
        {
            "form": form,
            "complaint_email": settings.COMPLAINT_RECIPIENT_EMAIL,
        },
        status=status,
    )
    response["Cache-Control"] = "no-store"
    if status == 429:
        response["Retry-After"] = "3600"
    return response
