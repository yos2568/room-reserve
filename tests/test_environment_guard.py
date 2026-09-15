"""Guard the test environment itself.

The suite has to run on ``roomreserve.settings.test``. pytest-django resolves the
settings module in this order: the ``--ds`` option, then the
``DJANGO_SETTINGS_MODULE`` environment variable, then the value in
``pyproject.toml``. So an exported ``DJANGO_SETTINGS_MODULE`` silently replaces the
test settings with development ones, and the suite then runs without test
isolation, without the in-memory mail and cache backends, and with the development
static storage.

That is not hypothetical: it happened while writing ``scripts/verify``, where an
exported ``DJANGO_SETTINGS_MODULE`` turned a green suite into one with dozens of
unrelated failures and no obvious cause. These two assertions make the next
occurrence fail immediately and by name.
"""

from __future__ import annotations

import os

from django.conf import settings

TEST_SETTINGS = "roomreserve.settings.test"


def test_the_suite_runs_on_the_test_settings():
    assert os.environ["DJANGO_SETTINGS_MODULE"] == TEST_SETTINGS, (
        "DJANGO_SETTINGS_MODULE was overridden in the environment; pytest-django "
        "prefers it over pyproject.toml, so the suite is not isolated"
    )
    assert settings.SETTINGS_MODULE == TEST_SETTINGS
    assert settings.DEBUG is False, "the test settings never run with DEBUG on"


def test_the_suite_uses_the_isolated_test_database():
    assert settings.DATABASES["default"]["NAME"] == "test_roomreserve"


def test_the_test_settings_do_not_use_the_production_static_manifest():
    """Plain storage, because the manifest only answers after collectstatic (D-06)."""
    storage = settings.STORAGES["staticfiles"]["BACKEND"]
    assert storage == "django.contrib.staticfiles.storage.StaticFilesStorage"


def test_the_test_clock_is_available_and_the_guard_is_documented():
    """The frozen clock is test infrastructure, never a public entrance."""
    assert settings.ALLOW_TEST_CLOCK is True
