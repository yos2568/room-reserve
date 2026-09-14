"""ASGI entry point. The project serves synchronous Django views."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roomreserve.settings.prod")

application = get_asgi_application()
