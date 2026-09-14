"""Production settings.

Every mandatory value is read with ``required=True`` so that a misconfigured
deployment fails at startup with a clear message instead of running with a demo
default (V3 section 10: "Production must fail clearly on missing mandatory
secrets, not use demo defaults").
"""

from __future__ import annotations

from roomreserve.env import env_bool, env_int, env_list, env_str
from roomreserve.settings.base import *  # noqa: F403

DEBUG = False

SECRET_KEY = env_str("DJANGO_SECRET_KEY", required=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must list the deployed hostnames in production."
    )

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not CSRF_TRUSTED_ORIGINS:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DJANGO_CSRF_TRUSTED_ORIGINS must list the https origins in production."
    )

# PostgreSQL credentials must be supplied; the base default is development-only.
POSTGRES_PASSWORD = env_str("POSTGRES_PASSWORD", required=True)
DATABASES["default"]["PASSWORD"] = POSTGRES_PASSWORD  # noqa: F405
DATABASES["default"]["CONN_MAX_AGE"] = env_int("DJANGO_CONN_MAX_AGE", 60)  # noqa: F405

# Mail must be configured explicitly in production; no catcher fallback.
EMAIL_HOST = env_str("EMAIL_HOST", required=True)
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", required=True)
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", required=True)
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", required=True)

# Caddy terminates TLS and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

# Development-only affordances that must never reach production.
ALLOW_TEST_CLOCK = False

# Do not let an SMTP outage fail process liveness and trigger restart loops
# (V3 section 11); the outbox worker surfaces the failure instead.
LOGGING["handlers"]["console"]["formatter"] = "structured"  # noqa: F405
