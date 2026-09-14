"""Test settings — PostgreSQL, deterministic clock, in-memory outbox capture.

The test clock is enabled here and only here (plus optionally in development).
V3 section 3.1: "A test clock may be injected only into test/development
infrastructure, never a public production endpoint."
"""

from __future__ import annotations

from roomreserve.env import env_str
from roomreserve.settings.base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-key-not-secret"

ALLOW_TEST_CLOCK = True

# Tests must run against PostgreSQL because the concurrency guarantees depend on
# row locks, partial unique indexes and serialization errors that SQLite does not
# reproduce (V3 section 12).
DATABASES["default"]["NAME"] = env_str("TEST_POSTGRES_DB", "test_roomreserve")  # noqa: F405
DATABASES["default"]["TEST"] = {"NAME": env_str("TEST_POSTGRES_DB", "test_roomreserve")}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "roomreserve@localhost.test"

# The database cache table is created by `manage.py createcachetable`, which the
# test database does not run, so tests use an in-process cache instead. Rate
# limiting is still exercised; it is simply not shared across processes.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "roomreserve-tests",
    }
}

OUTBOX_ENABLED = True
OUTBOX_LEASE_SECONDS = 30

# Faster, but still bounded, lock waits so a race test fails quickly.
BOOKING_LOCK_TIMEOUT_SECONDS = 5

LOGGING["root"]["level"] = "ERROR"  # noqa: F405
