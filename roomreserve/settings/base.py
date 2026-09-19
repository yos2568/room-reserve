"""Settings shared by every environment.

Environment-specific modules (dev/prod/test) import * from here and then apply
their own deltas. Anything mandatory in production is left without a default in
the module that requires it, so a missing value fails loudly (V3 section 10).
"""

from __future__ import annotations

from pathlib import Path

from django.utils.translation import gettext_lazy as _

from roomreserve.env import env_bool, env_int, env_list, env_str

BASE_DIR = Path(__file__).resolve().parent.parent.parent

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "[::1]"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])
STAFF_ALLOWED_IPS = env_list("STAFF_ALLOWED_IPS", [])
MAINTAINER_ALLOWED_IPS = env_list("MAINTAINER_ALLOWED_IPS", [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.MaintainerAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.RequestContextMiddleware",
    "core.middleware.PersonalResponseCacheMiddleware",
]

ROOT_URLCONF = "roomreserve.urls"
WSGI_APPLICATION = "roomreserve.wsgi.application"
ASGI_APPLICATION = "roomreserve.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_context",
            ],
        },
    },
]

# --- Database -----------------------------------------------------------------
# PostgreSQL 16 in every environment; SQLite is never used (V3 section 2).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env_str("POSTGRES_DB", "roomreserve"),
        "USER": env_str("POSTGRES_USER", "roomreserve"),
        "PASSWORD": env_str("POSTGRES_PASSWORD", "roomreserve"),
        "HOST": env_str("POSTGRES_HOST", "localhost"),
        "PORT": env_str("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DJANGO_CONN_MAX_AGE", 0),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"

# --- Cache --------------------------------------------------------------------
# Database-backed so rate-limit counters are shared across gunicorn workers
# without adding Redis to the stack (V3 section 2). Create the table with
# `manage.py createcachetable`, which the bootstrap and verify scripts run.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "roomreserve_cache",
        "TIMEOUT": 300,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = ["core.auth.InstitutionalIdBackend"]

# --- Internationalisation -----------------------------------------------------
# Thai is the default; English is offered as a toggle. Years are Gregorian (ค.ศ.).
LANGUAGE_CODE = "th"
LANGUAGES = [("th", _("Thai")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = "Asia/Bangkok"

# --- Sessions and cookies -----------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # HTMX reads the token from the DOM.
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = env_int("DJANGO_SESSION_COOKIE_AGE", 60 * 60 * 12)
LANGUAGE_COOKIE_HTTPONLY = True

LOGIN_URL = "core:login"
LOGIN_REDIRECT_URL = "core:my_bookings"
LOGOUT_REDIRECT_URL = "core:home"

# --- Static files -------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Email --------------------------------------------------------------------
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 1025)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", "roomreserve@example.invalid")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Set to False by the deployment when the mail provider is unavailable; outbox
# rows then stay queued instead of being drained (V3 sections 6 and 11).
OUTBOX_ENABLED = env_bool("OUTBOX_ENABLED", True)

# --- Operational policy defaults ---------------------------------------------
# These are application policy choices, NOT requirements of the source PDF
# (V3 section 1). They are snapshotted into a PolicyVersion row on first use and
# are editable through staff screens, except the fixed hourly slot geometry.
HORIZON_DAYS = env_int("POLICY_HORIZON_DAYS", 7)
DAILY_QUOTA = env_int("POLICY_DAILY_QUOTA", 2)
CHECKIN_GRACE_MINUTES = env_int("POLICY_CHECKIN_GRACE_MINUTES", 15)
STRIKE_WINDOW_DAYS = env_int("POLICY_STRIKE_WINDOW_DAYS", 30)
STRIKE_THRESHOLD = env_int("POLICY_STRIKE_THRESHOLD", 3)
AUTO_SUSPENSION_DAYS = env_int("POLICY_AUTO_SUSPENSION_DAYS", 7)
REMINDER_LEAD_MINUTES = env_int("POLICY_REMINDER_LEAD_MINUTES", 30)
SLOT_MINUTES = 60
OPENING_SLOT_START_HOUR = 8
LAST_SLOT_START_HOUR = 19
# The practice rooms are the nine numbered stalls on floor 3 of the Arts
# Building (ห้อง 1–9 on the department's floor plan). The two larger teaching
# rooms at the back of the floor join them via ROOM_OVERRIDES, because they
# carry building room numbers rather than stall numbers.
ROOM_COUNT = 9

# Per-room configuration applied by `manage.py seed_rooms`, keyed by room number.
# V3 says "nine rooms"; the department added rooms 303 and 304 from the floor
# plan, which is recorded as a deviation from V3 in docs/decisions.md (D-28).
# Room 303 is kept for piano and percussion students to *reserve* — anyone may
# walk in once an hour has started and the room is still free. Room 304 is a
# general practice room outside its class hours (ROOM_WEEKLY_BLOCKS below).
ROOM_OVERRIDES = {
    "303": {
        "label": "ห้อง 303",
        "position": 10,
        "reservation_scope": "LISTED",
        "categories": ("PIANO", "PERCUSSION"),
    },
    "304": {
        "label": "ห้อง 304 (ห้องบรรยาย 1)",
        "position": 11,
    },
}

# Recurring weekly hours a room is unavailable because a class meets in it: the
# department's teaching timetable (ตารางห้อง อาคารศิลปกรรมชั้น3.pdf, ภาคการศึกษาต้น
# 2569, page "A304 ห้องบรรยาย1"). Weekday is Monday=0; end_hour is exclusive.
# The hours were read from the sheet's own column geometry (D-28) and should be
# confirmed with the department. The timetable has no page for A303, so room 303
# has no blocked hours. Blocked hours refuse reservations *and* walk-ins.
ROOM_WEEKLY_BLOCKS = {
    "304": [
        {"weekday": 0, "start_hour": 10, "end_hour": 12, "reason": "COUNTERPOINT · อ.ดร.ปริญญา"},
        {"weekday": 1, "start_hour": 12, "end_hour": 13, "reason": "SKILL-PIANO · ผศ.ดร.รามสูร"},
        {"weekday": 3, "start_hour": 10, "end_hour": 12, "reason": "HARMONY · อ.ดร.ปริญญา"},
    ],
}

INSTITUTION_EMAIL_DOMAIN = env_str("INSTITUTION_EMAIL_DOMAIN", "student.chula.ac.th")

# Absolute base used to build links in transactional email, where there is no
# request object to call build_absolute_uri on (the scheduler sends them).
SITE_BASE_URL = env_str("SITE_BASE_URL", "http://localhost:8000")

SUPPORT_CONTACT_NAME = env_str("SUPPORT_CONTACT_NAME", "คุณสิตานันท์ (พี่ดิว)")
SUPPORT_CONTACT_PHONE = env_str("SUPPORT_CONTACT_PHONE", "02-218-4604")
SUPPORT_CONTACT_EMAIL = env_str("SUPPORT_CONTACT_EMAIL", "Sitanun.S@chula.ac.th")

# --- Outbox delivery ----------------------------------------------------------
OUTBOX_MAX_ATTEMPTS = env_int("OUTBOX_MAX_ATTEMPTS", 6)
OUTBOX_BACKOFF_MINUTES = [1, 5, 30, 60, 240]
OUTBOX_LEASE_SECONDS = env_int("OUTBOX_LEASE_SECONDS", 300)
OUTBOX_BATCH_SIZE = env_int("OUTBOX_BATCH_SIZE", 50)

# --- Scheduler ----------------------------------------------------------------
SCHEDULER_HEARTBEAT_STALE_SECONDS = env_int("SCHEDULER_HEARTBEAT_STALE_SECONDS", 180)
OUTBOX_AGE_WARN_SECONDS = env_int("OUTBOX_AGE_WARN_SECONDS", 600)

# --- Locking ------------------------------------------------------------------
# Bounded wait for the shared BookingControl row; on expiry the caller gets a
# translated "busy, try again" response instead of hanging (V3 section 5).
BOOKING_LOCK_TIMEOUT_SECONDS = env_int("BOOKING_LOCK_TIMEOUT_SECONDS", 3)
RECONCILE_BUDGET = env_int("RECONCILE_BUDGET", 500)

# --- Test clock ---------------------------------------------------------------
# Only test/dev settings may turn this on. No public endpoint can reach it.
ALLOW_TEST_CLOCK = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "structured"},
    },
    "root": {"handlers": ["console"], "level": env_str("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "core": {"handlers": ["console"], "level": env_str("DJANGO_LOG_LEVEL", "INFO")},
    },
}
