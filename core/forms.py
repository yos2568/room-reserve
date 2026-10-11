"""Guided staff decisions, with server-side validation and audited reasons."""

from django import forms
from django.utils.translation import gettext_lazy as _

from core.models import User


class EligibilityDecisionForm(forms.Form):
    REASONS = {
        User.Eligibility.APPROVED: (
            ("roster_verified", _("Verified against department roster")),
            ("identity_confirmed", _("Student identity confirmed by staff")),
            ("department_confirmed", _("Membership confirmed by department")),
        ),
        User.Eligibility.REJECTED: (
            ("not_eligible", _("Not eligible for department access")),
            ("incorrect_details", _("Registration details incorrect")),
            ("duplicate_account", _("Duplicate account")),
        ),
    }
    reason_choice = forms.ChoiceField(label=_("Reason (required)"))
    reason = forms.CharField(
        label=_("Details for Other"),
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2, "class": "field"}),
    )

    def __init__(self, *args, decision, user_id, **kwargs):
        super().__init__(*args, auto_id=f"decision-{decision}-{user_id}-%s", **kwargs)
        self.decision = decision
        self.fields["reason_choice"].choices = [
            ("", _("Choose a reason")),
            *self.REASONS.get(decision, ()),
            ("other", _("Other — enter details")),
        ]
        self.fields["reason_choice"].widget.attrs["class"] = "field"

    def clean(self):
        cleaned = super().clean()
        if self.decision not in self.REASONS:
            self.add_error("reason_choice", _("Choose Approve or Reject."))
        if cleaned.get("reason_choice") == "other" and not cleaned.get("reason"):
            self.add_error("reason", _("Enter details when choosing Other."))
        return cleaned

    def audit_reason(self):
        choice = self.cleaned_data["reason_choice"]
        if choice == "other":
            return self.cleaned_data["reason"]
        # Stable English audit text, independent of the staff member's language.
        from django.utils.translation import override

        with override("en"):
            label = str(dict(self.REASONS[self.decision])[choice])
        details = self.cleaned_data.get("reason")
        return f"{label}: {details}" if details else label
