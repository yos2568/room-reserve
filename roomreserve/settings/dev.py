"""Development settings — local Docker Compose, mail catcher, debug on.

Never used for a public deployment. The development secret key is deliberately
obvious so that a copy-pasted value in production is recognisable as a mistake.
"""

from __future__ import annotations

from roomreserve.env import env_bool, env_int, env_list, env_str
from roomreserve.settings.base import *  # noqa: F403

DEBUG = True

SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-only-insecure-key-not-for-deployment")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "[::1]", "0.0.0.0"])  # noqa: F405

# Development uses the private mail catcher (Mailpit) started by Compose, so the
# outbox worker exercises a real SMTP conversation locally (V3 section 10).
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 1025)  # noqa: F405
EMAIL_USE_TLS = False
EMAIL_USE_SSL = False
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", "roomreserve@localhost.test")

INTERNAL_IPS = ["127.0.0.1"]

# Permitted in development infrastructure only; still off unless explicitly set.
ALLOW_TEST_CLOCK = env_bool("ALLOW_TEST_CLOCK", False)
