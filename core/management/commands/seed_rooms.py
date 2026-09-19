"""Idempotently ensure the configured practice rooms exist.

The rooms and their per-room configuration come from settings
(``ROOM_COUNT`` / ``ROOM_OVERRIDES`` / ``ROOM_WEEKLY_BLOCKS``): the nine stalls,
the piano-and-percussion room 303, and the teaching room 304 with its class
hours are deployment configuration rather than literals buried in a command.
Rooms outside the configured set are deactivated, never deleted.
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
        parts = [f"rooms ready: {report['total']} total, {report['created']} created"]
        if report.get("retired"):
            parts.append(f"{report['retired']} deactivated")
        if report.get("held_out"):
            parts.append(f"left out of service by staff: {', '.join(report['held_out'])}")
        self.stdout.write(self.style.SUCCESS(", ".join(parts)))
