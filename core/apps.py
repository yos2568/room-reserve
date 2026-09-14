from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Room Reserve"

    def ready(self) -> None:
        # Importing registers the group-sync signal handlers that keep the
        # operational-staff flag aligned with the staff group.
        from core import signals  # noqa: F401
        from core.services.clock import freeze_from_environment

        # Inert unless ALLOW_TEST_CLOCK is on; used to pin browser fixtures to a
        # fixed time (V3 section 12).
        freeze_from_environment()
