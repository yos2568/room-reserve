"""Public-surface inspection and the printable posters (V3 A26 and A29).

A26: "Public HTML/JSON/log inspection finds no student identities/secrets;
personal responses not publicly cached; CSV formula payload safe."

A29: "Nine posters render legibly and QR URLs resolve correct rooms; deployment
physical door mapping is a separate manual gate."

The QR half of A29 is verified by regenerating each poster's code from the target
it should carry and comparing bytes. No QR *decoder* is installed, so reading the
printed sheet back is a deployment check, not something claimed here.
"""

from __future__ import annotations

import base64
import re
import struct

import pytest
from django.conf import settings
from django.core.management import call_command
from playwright.sync_api import expect

from core.services.posters import QR_BORDER, QR_SCALE, qr_png_bytes, qr_target_for
from tests import browserlib, factories
from tests.browserlib import FACTORY_PASSWORD, sign_in, visit

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

TODAY = browserlib.MONDAY_DATE

# Print legibility is a module-size question, not a pixel-count one: a door
# poster is scanned from arm's length, and 1 mm per module is comfortably inside
# what a phone camera resolves.
MIN_PRINTED_MODULE_MM = 1.0
CSS_PX_PER_INCH = 96

PUBLIC_PATHS = (
    "th/",
    "en/",
    "th/availability/",
    "th/rules/",
    "th/help/",
    "th/privacy/",
    "th/login/",
    "th/register/",
    "th/password-reset/",
)


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Width and height straight from the PNG header, without an image library."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", data[16:24])


def module_count(width_px: int) -> int:
    """QR modules across a rendered image, from the generator's own geometry."""
    modules = width_px / QR_SCALE - 2 * QR_BORDER
    assert modules == int(modules), f"{width_px}px is not a whole number of modules"
    return int(modules)


def test_no_public_page_leaks_an_identity_or_a_secret(rooms, live_server, browser_clock, student, watched):
    """Anonymous pages, scanned for the identifiers and secrets that exist."""
    page = watched.page
    student.name_th = "ชื่อลับ สำหรับทดสอบการรั่วไหล"
    student.save(update_fields=["name_th"])
    factories.make_booking(student, rooms[0], day=TODAY, hour=13)

    forbidden = [
        student.name_th,
        student.name_en,
        student.institutional_id,
        student.email,
        str(settings.SECRET_KEY),
    ]

    for path in PUBLIC_PATHS:
        page.goto(f"{live_server.url}/{path}", wait_until="load")
        body = page.content()
        for needle in forbidden:
            assert needle not in body, f"{path} leaked {needle!r}"

    # The probes are outside the language prefix and must stay minimal.
    for probe in ("healthz/", "readyz/"):
        response = page.request.get(f"{live_server.url}/{probe}")
        assert response.status == 200
        text = response.text()
        assert len(text) < 400, f"{probe} returns more than a status"
        for needle in forbidden:
            assert needle not in text

    watched.assert_clean()


def test_a_personal_response_is_not_publicly_cacheable(rooms, live_server, browser_clock, student, watched):
    """V3 section 10: no personal data in a public cache."""
    page = watched.page
    base = live_server.url

    anonymous = page.request.get(f"{base}/th/")
    anon_cache = anonymous.headers.get("cache-control", "")
    assert "public" not in anon_cache, "a page carrying the CSRF cookie must not be public"
    assert "cookie" in anonymous.headers.get("vary", "").lower()

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    personal = page.request.get(f"{base}/th/my-bookings/")
    assert personal.status == 200

    cache = personal.headers.get("cache-control", "")
    assert "private" in cache
    assert "no-store" in cache, f"a personalized response must not be storable: {cache!r}"
    assert "cookie" in personal.headers.get("vary", "").lower()

    watched.assert_clean()


def test_one_poster_per_room_is_generated_and_each_carries_its_own_room(
    rooms, live_server, browser_clock, staff_user, tmp_path, watched
):
    """A29: one poster per active room, each encoding that room's own target."""
    base = live_server.url
    call_command("generate_posters", str(tmp_path), "--base-url", base)

    pngs = sorted(tmp_path.glob("*.png"))
    # One per room, not a hard-coded nine: the department added a tenth room and
    # the assertion has to follow the data rather than a literal.
    assert len(pngs) == len(rooms), f"one poster per room, got {[p.name for p in pngs]}"

    for room in rooms:
        expected_target = qr_target_for(room, base)
        matching = [png for png in pngs if png.read_bytes() == qr_png_bytes(expected_target)]
        assert matching, f"no poster encodes {expected_target}"

        width, height = png_dimensions(matching[0].read_bytes())
        assert width == height, f"{matching[0].name} is not square"
        assert module_count(width) >= 21, "a QR code is at least 21 modules across"

    sheet = next(iter(tmp_path.glob("*.html"))).read_text(encoding="utf-8")
    for room in rooms:
        assert f"/r/{room.pk}/" in sheet
        assert str(room) in sheet

    # And each QR target resolves to that room, signed in, on a real browser.
    page = watched.page
    sign_in(page, base, staff_user.institutional_id, FACTORY_PASSWORD)
    for room in rooms:
        page.goto(f"{base}/th/r/{room.pk}/", wait_until="load")
        assert str(room) in page.locator("h1").first.inner_text()

    watched.assert_clean()


def test_the_printable_sheet_renders_one_legible_poster_per_room(
    rooms, live_server, browser_clock, staff_user, watched
):
    """The printable sheet is a real page, not a 500 and not a blank grid."""
    page = watched.page
    base = live_server.url
    sign_in(page, base, staff_user.institutional_id, FACTORY_PASSWORD)

    visit(page, base, "staff/posters/", lang="en")
    expect(page.locator("main")).to_contain_text(f"/r/{rooms[0].pk}/")

    page.goto(f"{base}/en/staff/posters/sheet/", wait_until="load")
    posters = page.locator("section.sheet")
    expect(posters).to_have_count(len(rooms))

    first = posters.first
    expect(first.locator("h1")).to_have_text(str(rooms[0]))
    expect(first).to_contain_text(f"/r/{rooms[0].pk}/")

    # The code is embedded and rendered at a printable size.
    image = first.locator("img")
    expect(image).to_have_count(1)
    source = image.get_attribute("src") or ""
    assert source.startswith("data:image/png;base64,")
    payload = base64.b64decode(source.split(",", 1)[1])
    width, height = png_dimensions(payload)
    assert width == height
    modules = module_count(width)
    assert modules >= 21

    box = image.bounding_box()
    assert box, "the QR image must be laid out"
    printed_mm = box["width"] / CSS_PX_PER_INCH * 25.4
    module_mm = printed_mm / modules
    assert module_mm >= MIN_PRINTED_MODULE_MM, (
        f"each module prints {module_mm:.2f}mm across, too small for a door poster"
    )

    # The stated opening hours and grace must actually resolve, not render blank.
    text = first.inner_text()
    assert f"{settings.OPENING_SLOT_START_HOUR}:00" in text
    assert ":00–:00" not in text
    assert re.search(r"within\s+\d+\s+minutes", text), f"the grace period is missing: {text!r}"

    watched.assert_clean()
