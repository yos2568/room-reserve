"""The content pages have to state the policy, not blank spaces.

Rules and Help tell a student when they may check in, how far ahead they may book
and what causes a suspension. Every one of those numbers comes from the policy
object, and a template that names a property the model does not define renders an
empty string rather than raising — so "closes exactly  minutes later" reaches the
reader with nothing failing anywhere.

The guard is the *absence of an unfilled placeholder* in the visible text, which
is language-independent: the number assertions alone would be weak, because a
single digit appears all over a page's HTML.
"""

from __future__ import annotations

import html
import re

import pytest
from django.urls import reverse
from django.utils import translation

from core.services.policy import current_policy

pytestmark = pytest.mark.django_db

TAG = re.compile(r"<[^>]+>")


@pytest.fixture
def policy(db):
    return current_policy()


def visible_text(client, route: str, lang: str) -> str:
    """The page as a reader sees it: tags removed, entities resolved."""
    with translation.override(lang):
        url = reverse(route)
    response = client.get(url)
    assert response.status_code == 200, f"{url} returned {response.status_code}"
    body = html.unescape(TAG.sub(" ", response.content.decode("utf-8")))
    return re.sub(r"\s+", " ", body)


# A number must be followed by its unit, in either language.
UNFILLED = (
    re.compile(r"\bwithin\s+(?:minutes|days)"),
    re.compile(r"\bexactly\s+(?:minutes|days)"),
    re.compile(r"รอบ\s+(?:นาที|วัน)"),
    re.compile(r"ภายใน\s+(?:นาที|วัน)"),
    re.compile(r":00–:00"),
    re.compile(r"\bof\s+(?:bookings|days)"),
)


@pytest.mark.parametrize("lang", ["th", "en"])
@pytest.mark.parametrize("route", ["core:rules", "core:help"])
def test_no_policy_number_renders_as_a_blank(client, policy, route, lang):
    text = visible_text(client, route, lang)
    for pattern in UNFILLED:
        assert not pattern.search(text), f"{route} ({lang}) rendered {pattern.pattern!r} unfilled"


@pytest.mark.parametrize("lang", ["th", "en"])
def test_the_rules_page_states_each_threshold_next_to_its_unit(client, policy, lang):
    text = visible_text(client, "core:rules", lang)

    assert re.search(rf"\b{policy.checkin_grace_minutes}\b\s*(minutes|นาที)", text), text
    assert re.search(rf"\b{policy.horizon_days}\b\s*(days|วัน)", text), text
    assert re.search(rf"\b{policy.strike_threshold}\b", text), text
    assert re.search(rf"\b{policy.strike_window_days}\b", text), text
    assert re.search(rf"\b{policy.auto_suspension_days}\b", text), text


@pytest.mark.parametrize("lang", ["th", "en"])
def test_the_help_page_states_the_check_in_window(client, policy, lang):
    text = visible_text(client, "core:help", lang)
    assert re.search(rf"\b{policy.checkin_grace_minutes}\b\s*(minutes|นาที)", text), text
