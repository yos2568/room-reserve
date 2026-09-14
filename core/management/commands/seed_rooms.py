"""Idempotently ensure the nine practice rooms exist."""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Room


class Command(BaseCommand):
    help = "Create or refresh the nine practice rooms. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=9)

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        created = 0
        for number in range(1, count + 1):
            _, was_created = Room.objects.get_or_create(
                number=str(number),
                defaults={"label": f"ห้องซ้อม {number}", "position": number},
            )
            created += int(was_created)

        # A room beyond the configured count is left alone rather than deleted:
        # bookings may reference it.
        self.stdout.write(
            self.style.SUCCESS(f"rooms ready: {Room.objects.count()} total, {created} created")
        )
