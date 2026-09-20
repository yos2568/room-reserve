"""Run one scheduler tick. The Compose scheduler service calls this every minute."""

from django.core.management.base import BaseCommand

from core.services.scheduler import run_tick


class Command(BaseCommand):
    help = "Run one scheduler tick: reconcile, enqueue reminders, drain the outbox, heartbeat."

    def handle(self, *args, **options):
        summary = run_tick()
        if summary.get("skipped"):
            self.stdout.write(f"skipped: {summary.get('reason')}")
            return
        reconciled = summary.get("reconciled", {})
        mail = summary.get("mail", {})
        self.stdout.write(
            "tick complete: "
            f"expired_approvals={reconciled.get('expired_approvals', 0)} "
            f"no_shows={reconciled.get('no_shows', 0)} "
            f"completions={reconciled.get('completions', 0)} "
            f"suspensions={reconciled.get('suspensions', 0)} "
            f"lifts={reconciled.get('lifts', 0)} "
            f"reminders={summary.get('reminders', 0)} "
            f"mail_claimed={mail.get('claimed', 0)} "
            f"mail_sent={mail.get('sent', 0)} "
            f"mail_failed={mail.get('failed', 0)}"
        )
