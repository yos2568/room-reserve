"""Import the department roster from a CSV file.

Thin wrapper over the validated import service, so the same rules apply from the
command line and from the staff screen. The service is all-or-nothing: a
contradiction with an existing entry aborts the whole file rather than
overwriting it.
"""

from __future__ import annotations

import pathlib

from django.core.management.base import BaseCommand, CommandError

from core.services.roster import deactivate_missing, import_roster_text


class Command(BaseCommand):
    help = "Validate and apply a roster CSV. Use --dry-run first."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the CSV file.")
        parser.add_argument("--dry-run", action="store_true", help="Validate without writing.")
        parser.add_argument("--batch", default="", help="Batch label recorded on new rows.")
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help=(
                "After a fully valid import, deactivate active roster entries that are "
                "absent from the file. Only correct when the file is a complete roster."
            ),
        )

    def handle(self, *args, **options):
        path = pathlib.Path(options["path"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Thai CSVs exported from Excel are often TIS-620.
            text = raw.decode("cp874", errors="replace")

        try:
            report = import_roster_text(
                text=text,
                actor=None,
                dry_run=options["dry_run"],
                batch=options["batch"],
            )
        except ValueError as exc:
            raise CommandError(f"Import rejected, nothing written: {exc}") from exc

        self.stdout.write(report.summary())
        for error in report.errors:
            self.stderr.write(f"  line {error.line}: {error.message}")

        if report.errors:
            raise CommandError("Import failed validation; no rows were written.")

        if options["deactivate_missing"]:
            keep = set(report.institutional_ids)
            count = deactivate_missing(keep_ids=keep, actor=None, dry_run=options["dry_run"])
            verb = "would be" if options["dry_run"] else "were"
            self.stdout.write(f"{count} stale entries {verb} deactivated.")
