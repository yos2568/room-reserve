"""WSGI entry point (gunicorn target in production)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roomreserve.settings.prod")

application = get_wsgi_application()
