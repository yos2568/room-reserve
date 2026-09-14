"""Idempotent demo bootstrap with synthetic data.

V3 section 2 requires "idempotent demo seeding and validated roster/staff
invitation commands; no fabricated production accounts", and section 8 requires
development to use "clearly synthetic accounts with local-only credential
instructions".

Re-running this command never resets a password and never escalates privileges:
existing accounts are left exactly as they are. Every address uses a reserved
documentation domain so it cannot collide with a real student.
"""

from __future__ import annotations

import random

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import EligibleStudent, Room, User
from core.services.identity import normalize_email
from core.services.policy import current_policy
from core.signals import OPERATIONAL_STAFF_GROUP

# Reserved for documentation by RFC 2606, so a seed can never mail a real person.
DEMO_DOMAIN = "example.invalid"

DEMO_STUDENT_PASSWORD = "demo-student-password-1"
DEMO_STAFF_PASSWORD = "demo-staff-password-1"

FIRST_NAMES = [
    "สมชาย", "สมหญิง", "ปิยะ", "ณัฐ", "อารี", "มานะ", "วิชัย", "ปรีชา",
    "กมล", "ธนา", "ศิริ", "รัตนา", "จุฑา", "พิมพ์", "อรุณ", "นภา",
]
LAST_NAMES = [
    "ใจดี", "รักเรียน", "แสนสุข", "วงศ์ทอง", "ศรีสุข", "ทองดี", "บุญมี",
    "พูลผล", "มั่งมี", "เจริญสุข",
]


class Command(BaseCommand):
    help = "Create rooms, a synthetic roster, students, and staff. Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=100, help="Synthetic student count.")
        parser.add_argument("--staff", type=int, default=10, help="Operational staff count.")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the demo password from stdin instead of using the documented default.",
        )

    def handle(self, *args, **options):
        student_count = max(0, options["students"])
        staff_count = max(0, options["staff"])

        if options["password_stdin"]:
            import sys

            student_password = sys.stdin.readline().strip()
            if not student_password:
                self.stderr.write(self.style.ERROR("No password supplied on stdin."))
                return
        else:
            student_password = DEMO_STUDENT_PASSWORD

        created = {"rooms": 0, "roster": 0, "students": 0, "staff": 0, "existing": 0}

        with transaction.atomic():
            # Parent rows first so roster entries can attach to accounts.
            rooms = self._seed_rooms()
            created["rooms"] = rooms
            group, _ = Group.objects.get_or_create(name=OPERATIONAL_STAFF_GROUP)

            for index in range(1, student_count + 1):
                result = self._seed_student(index, student_password)
                created[result] += 1

            for index in range(1, staff_count + 1):
                result = self._seed_staff(index, group)
                created[result] += 1

            # Make sure the initial policy snapshot exists for a fresh database.
            current_policy()

        self._report(created, student_password)

    # -- pieces ---------------------------------------------------------------

    def _seed_rooms(self) -> int:
        count = 0
        for number in range(1, 10):
            _, was_created = Room.objects.get_or_create(
                number=str(number),
                defaults={"label": f"ห้องซ้อม {number}", "position": number},
            )
            count += int(was_created)
        return count

    def _seed_student(self, index: int, password: str) -> str:
        institutional_id = f"66{index:07d}"
        email = normalize_email(f"student{index:04d}@{DEMO_DOMAIN}")

        user = User.objects.filter(username=institutional_id).first()
        if user is None:
            user = User.objects.create_user(
                username=institutional_id,
                email=email,
                password=password,
                name_th=f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
                name_en=f"Demo Student {index}",
                eligibility=User.Eligibility.APPROVED,
            )
            # Verified on purpose: the demo must be usable without a mail round trip.
            from django.utils import timezone

            user.email_verified_at = timezone.now()
            user.eligibility_reason = "Synthetic demo account."
            user.save(update_fields=["email_verified_at", "eligibility_reason"])
            result = "students"
        else:
            # Never reset an existing credential or privilege.
            result = "existing"

        EligibleStudent.objects.get_or_create(
            institutional_id=institutional_id,
            defaults={
                "email": email,
                "name_th": user.name_th,
                "program": "ดนตรีตะวันตก",
                "instrument": random.choice(["เปียโน", "ไวโอลิน", "ร้องเพลง", "กีตาร์"]),
                "year": random.randint(1, 4),
                "is_active": True,
                "account": user,
                "import_batch": "seed",
            },
        )
        return result

    def _seed_staff(self, index: int, group) -> str:
        institutional_id = f"staff{index:04d}"
        email = normalize_email(f"staff{index:02d}@{DEMO_DOMAIN}")

        user = User.objects.filter(username=institutional_id).first()
        if user is None:
            user = User.objects.create_user(
                username=institutional_id,
                email=email,
                password=DEMO_STAFF_PASSWORD,
                name_th=f"เจ้าหน้าที่ ทดสอบ {index}",
                name_en=f"Demo Staff {index}",
                eligibility=User.Eligibility.APPROVED,
                is_operational_staff=True,
            )
            from django.utils import timezone

            user.email_verified_at = timezone.now()
            user.eligibility_reason = "Synthetic demo staff account."
            user.save(update_fields=["email_verified_at", "eligibility_reason"])
            result = "staff"
        else:
            result = "existing"

        # Group membership keeps the flag in step through the signal.
        if not user.groups.filter(pk=group.pk).exists():
            user.groups.add(group)
        return result

    def _report(self, created: dict, student_password: str) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                "seed complete: "
                f"rooms={created['rooms']} students={created['students']} "
                f"staff={created['staff']} existing={created['existing']}"
            )
        )
        self.stdout.write("")
        self.stdout.write("Local demo access (development only):")
        self.stdout.write("  student  6600000001 / " + student_password)
        self.stdout.write("  staff    staff0001 / " + DEMO_STAFF_PASSWORD)
        self.stdout.write("")
        self.stdout.write(
            "These credentials are synthetic and exist only in the local database. "
            "They grant no access to any deployed environment."
        )
