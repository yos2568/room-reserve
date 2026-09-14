"""Create the single technical maintainer account.

V3 section 1: ten staff accounts share operational permissions, and "only the
technical maintainer receives superuser/deployment privileges". This command is
the only supported way to create a superuser, and it never creates the ten staff
accounts.
"""

from __future__ import annotations

import getpass
import sys

from django.core.management.base import BaseCommand, CommandError

from core.models import User

DEFAULT_ID = "maintainer"


class Command(BaseCommand):
    help = "Create the technical maintainer (superuser). Prompts for a password."

    def add_arguments(self, parser):
        parser.add_argument("--id", default=DEFAULT_ID, help="Institutional ID for the maintainer.")
        parser.add_argument("--email", required=True, help="Contact email for the maintainer.")
        parser.add_argument(
            "--password-stdin",
            action="store_true",
            help="Read the password from stdin (for scripted, non-interactive setups).",
        )

    def handle(self, *args, **options):
        if User.objects.filter(is_superuser=True).exists():
            raise CommandError(
                "A superuser already exists. Refusing to create a second maintainer; "
                "use the existing account or demote it deliberately."
            )

        institutional_id = options["id"].strip()
        if User.objects.filter(username=institutional_id).exists():
            raise CommandError(f"{institutional_id} already exists as an account.")

        if options["password_stdin"]:
            password = sys.stdin.readline().strip()
        else:
            password = getpass.getpass("Maintainer password: ")
            confirm = getpass.getpass("Repeat password: ")
            if password != confirm:
                raise CommandError("Passwords did not match.")

        if not password:
            raise CommandError("A password is required.")

        user = User.objects.create_superuser(
            username=institutional_id,
            email=options["email"],
            password=password,
            name_th="ผู้ดูแลระบบ",
        )
        self.stdout.write(self.style.SUCCESS(f"Maintainer {user.institutional_id} created."))
        self.stdout.write("This account should not be used for day-to-day staff work.")
