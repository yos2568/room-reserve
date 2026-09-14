"""Root URL configuration.

Health probes and the language switcher stay outside the language prefix so that
external monitoring has one stable URL, and every user-facing route is available
in both Thai and English (V3 sections 9 and 11).
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import health

urlpatterns = [
    path("healthz/", health.liveness, name="healthz"),
    path("readyz/", health.readiness, name="readyz"),
    path("i18n/", include("django.conf.urls.i18n")),
]

urlpatterns += i18n_patterns(path("", include("core.urls")))

# Django admin is the technical maintainer's surface only; staff operations live
# under /staff/. Keeper of the deployment, not of the day-to-day workflow.
urlpatterns += [path("maintainer/", admin.site.urls)]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
