"""Invite operational staff accounts.

Staff identities are supplied by the owner (V3 section 8). This command creates
accounts with the staff group and a single-use activation link; it never grants
superuser and never resets an existing password.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.services.identity import invite_account
from core.services.policy import current_policy


class Command(BaseCommand):
    help = "Create or re-invite operational staff accounts and print their activation links."

    def add_arguments(self, parser):
        parser.add_argument(
            "identities",
            nargs="+",
            help="One or more entries as institutional_id:email[:name]",
        )

    def handle(self, *args, **options):
        current_policy()
        failures = 0
        for entry in options["identities"]:
            parts = entry.split(":")
            if len(parts) < 2:
                self.stderr.write(f"skipped {entry!r}: expected institutional_id:email[:name]")
                failures += 1
                continue
            institutional_id, email = parts[0], parts[1]
            name = parts[2] if len(parts) > 2 else ""

            try:
                user, invitation, raw_token = invite_account(
                    institutional_id=institutional_id,
                    email=email,
                    name=name,
                    actor=None,
                    staff=True,
                    reason="Created by invite_staff command.",
                )
            except Exception as exc:
                self.stderr.write(f"failed {institutional_id}: {exc}")
                failures += 1
                continue

            self.stdout.write(f"{user.institutional_id} <{user.email}>")
            self.stdout.write(f"  activation path: /invite/{raw_token}/")
            self.stdout.write("  shown once; also queued for delivery by the outbox worker.")

        if failures:
            self.stderr.write(self.style.WARNING(f"{failures} entr(ies) failed."))
        else:
            self.stdout.write(self.style.SUCCESS("Invitations queued."))
