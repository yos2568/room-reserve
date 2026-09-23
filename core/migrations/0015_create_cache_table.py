"""Create the database cache table as part of ``migrate``.

The rate limiter behind login, registration and password reset uses the
DatabaseCache table ``roomreserve_cache``. ``createcachetable`` is a separate
command that deployments never ran, so every rate-limited form returned a 500
in production. Tests use an in-process cache and could not catch it.
``createcachetable`` is idempotent and skips caches that are not database-backed.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_weekly_block_dated_meetings"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
