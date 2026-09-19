"""QR poster generation.

Nine printable A4 posters, one per room, each carrying the room label, a short
URL, instructions and the support contact (V3 section 9). ``segno`` writes PNG
directly, so no image library is needed at runtime.

A generated QR target is evidence that the code encodes the right URL. Whether
the printed sheet is legible and stuck to the correct door is a separate physical
check that cannot be automated, and is reported as such.
"""

from __future__ import annotations

import base64
import io

import segno

# Printed at arm's length on a door: keep the modules large and the contrast high.
QR_SCALE = 8
QR_BORDER = 4

# Inside an email, a compact code is plenty: it is read from a phone screen (D-31).
QR_EMAIL_SCALE = 4
QR_EMAIL_BORDER = 2


def qr_data_uri(target: str, *, scale: int = QR_EMAIL_SCALE, border: int = QR_EMAIL_BORDER) -> str:
    """An inline PNG data URI, small enough to embed in an HTML email (D-31)."""
    qr = segno.make(target, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border, dark="#000000", light="#ffffff")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def qr_png_bytes(target: str, *, scale: int = QR_SCALE, border: int = QR_BORDER) -> bytes:
    """A PNG QR code for an absolute URL."""
    qr = segno.make(target, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border, dark="#000000", light="#ffffff")
    return buffer.getvalue()


def qr_svg_markup(target: str, *, scale: int = QR_SCALE, border: int = QR_BORDER) -> str:
    """Inline SVG, for posters that must stay crisp when scaled by a browser."""
    qr = segno.make(target, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=scale, border=border, dark="#000000", light="#ffffff")
    return buffer.getvalue().decode("utf-8")


def qr_target_for(room, base_url: str) -> str:
    """The stable printed URL for a room."""
    return f"{base_url.rstrip('/')}/r/{room.pk}/"


def validate_target(target: str, expected_room_id: int) -> bool:
    """Confirm the encoded URL points at the intended room.

    Used by tests to assert that what is printed is what resolves.
    """
    marker = f"/r/{expected_room_id}/"
    return marker in target
