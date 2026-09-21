"""Regressions found by driving the app in a browser (22 September 2026).

The automated suite passed while a phone user saw twelve identical "Reserve"
buttons with no hour, the colour key said crimson meant "booked" while every free
hour was a crimson button, and about 190 Thai strings were missing or fuzzy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import translation

pytestmark = pytest.mark.django_db


def _home_html() -> str:
    with translation.override("th"):
        url = reverse("core:home")
    return Client().get(url).content.decode("utf-8")


def _mobile_list(html: str) -> str:
    start = html.index('<div class="space-y-3 md:hidden">')
    return html[start:]


def test_every_phone_cell_shows_its_hour(frozen, rooms):
    mobile = _mobile_list(_home_html())

    cells = re.findall(r'<li class="flex flex-col gap-1">\s*<span[^>]*>(\d{2}:\d{2})</span>', mobile)

    assert cells, "the phone list rendered no hour labels"
    assert "08:00" in cells and "19:00" in cells


def test_free_hours_use_the_free_style_not_the_booked_crimson(frozen, rooms):
    html = _home_html()

    reserve_links = re.findall(r'<a class="([^"]*)"\s+href="/th/book/', html)

    assert reserve_links, "no reservable hours rendered"
    assert all("slot-reserve" in classes for classes in reserve_links)
    assert not any("btn-primary" in classes for classes in reserve_links)


def test_the_thai_catalogue_has_no_empty_or_fuzzy_entries():
    po = Path(settings.BASE_DIR, "locale", "th", "LC_MESSAGES", "django.po").read_text(encoding="utf-8")
    blocks = po.split("\n\n")[1:]  # skip the header

    fuzzy = [b for b in blocks if re.search(r"^#, .*fuzzy", b, re.M) and not b.startswith("#~")]
    empty = [
        b
        for b in blocks
        if not b.startswith("#~")
        and re.search(r'^msgstr ""\s*$', b, re.M)
        and not re.search(r'^msgstr ""\n"', b, re.M)
    ]

    assert not fuzzy, f"{len(fuzzy)} fuzzy entries fall back to English:\n{fuzzy[0][:200]}"
    assert not empty, f"{len(empty)} untranslated entries:\n{empty[0][:200]}"
