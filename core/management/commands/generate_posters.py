"""Write nine printable QR posters to a directory.

Produces a self-contained HTML file (one A4 page per room) plus the raw PNG for
each room, so the sheet can be printed from a browser without the app running.
"""

from __future__ import annotations

import base64
import html
import pathlib

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Room
from core.services.posters import qr_png_bytes, validate_target


class Command(BaseCommand):
    help = "Generate one QR poster per active room into an output directory."

    def add_arguments(self, parser):
        parser.add_argument("output", help="Directory to write into.")
        parser.add_argument(
            "--base-url",
            default=settings.SITE_BASE_URL,
            help="Public base URL the QR codes should point at.",
        )

    def handle(self, *args, **options):
        output = pathlib.Path(options["output"])
        output.mkdir(parents=True, exist_ok=True)
        base_url = options["base_url"].rstrip("/")

        rooms = list(Room.objects.filter(is_active=True).order_by("position"))
        if not rooms:
            self.stderr.write(self.style.ERROR("No active rooms; run seed_rooms first."))
            return

        pages = []
        for room in rooms:
            target = f"{base_url}/r/{room.pk}/"
            if not validate_target(target, room.pk):
                self.stderr.write(self.style.ERROR(f"Refusing: {target} does not match room {room.pk}."))
                continue

            png = qr_png_bytes(target)
            (output / f"room-{room.number}.png").write_bytes(png)
            pages.append(
                {
                    "room": room,
                    "target": target,
                    "qr": base64.b64encode(png).decode("ascii"),
                }
            )
            self.stdout.write(f"{room}: {target}")

        sheet = output / "posters.html"
        sheet.write_text(_render(pages), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(pages)} posters and {sheet}"))
        self.stdout.write(
            "Legibility and door mapping are physical checks and are not established here."
        )


def _render(pages: list[dict]) -> str:
    from django.conf import settings

    blocks = []
    for page in pages:
        room = html.escape(str(page["room"]))
        target = html.escape(page["target"])
        blocks.append(
            f"""
  <section class="sheet">
    <header>
      <p class="dept">Western Music Department</p>
      <h1>{room}</h1>
      <p class="lead">Scan to check in or use this room</p>
    </header>
    <img src="data:image/png;base64,{page["qr"]}" alt="QR code for {room}">
    <ol>
      <li>If you booked this hour, check in within {settings.CHECKIN_GRACE_MINUTES} minutes of the start.</li>
      <li>If the hour is free, confirm you are here to use it now.</li>
      <li>The session ends at the hour boundary.</li>
    </ol>
    <p class="url">{target}</p>
    <p class="contact">{html.escape(settings.SUPPORT_CONTACT_NAME)} · {html.escape(settings.SUPPORT_CONTACT_PHONE)} · {html.escape(settings.SUPPORT_CONTACT_EMAIL)}</p>
    <footer>Scanning shows that an account action happened. It does not prove you are in the room.</footer>
  </section>"""
        )

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>Room Reserve posters</title>
<style>
  @page {{ size: A4 portrait; margin: 12mm; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; color: #111; }}
  .sheet {{ page-break-after: always; min-height: 260mm; display: flex; flex-direction: column;
            align-items: center; justify-content: space-between; text-align: center; padding: 6mm; }}
  .sheet:last-child {{ page-break-after: auto; }}
  .dept {{ text-transform: uppercase; letter-spacing: .08em; font-size: 12px; color: #555; }}
  h1 {{ font-size: 46px; margin: 8px 0; }}
  .lead {{ font-size: 22px; margin: 0; }}
  img {{ width: 300px; height: 300px; }}
  ol {{ text-align: left; max-width: 150mm; font-size: 16px; line-height: 1.5; }}
  .url {{ font-size: 13px; color: #444; word-break: break-all; }}
  .contact {{ font-size: 16px; }}
  footer {{ font-size: 12px; color: #555; max-width: 150mm; }}
</style>
</head>
<body>
{"".join(blocks)}
</body>
</html>
"""
