"""Pre-seed the BookingControl singleton row.

V3 section 4 requires BookingControl to be a "singleton pre-seeded row, shared
first lock for operational writes". It must exist before the first request, so it
is created here rather than by a seed command that a deployment might skip.
"""

from django.db import migrations


def create_control(apps, schema_editor):
    BookingControl = apps.get_model("core", "BookingControl")
    BookingControl.objects.get_or_create(pk=1)


def remove_control(apps, schema_editor):
    BookingControl = apps.get_model("core", "BookingControl")
    BookingControl.objects.filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(create_control, remove_control)]
