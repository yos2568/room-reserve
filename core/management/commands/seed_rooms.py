"""Idempotently ensure the configured practice rooms exist.

The count and the per-room configuration come from settings
(``ROOM_COUNT`` / ``ROOM_OVERRIDES``), so the tenth room and its audience are
deployment configuration rather than a literal buried in a command.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from core.services.rooms import ensure_rooms


class Command(BaseCommand):
    help = "Create or refresh the practice rooms. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=settings.ROOM_COUNT)

    def handle(self, *args, **options):
        report = ensure_rooms(count=options["count"])
        self.stdout.write(
            self.style.SUCCESS(f"rooms ready: {report['total']} total, {report['created']} created")
        )
