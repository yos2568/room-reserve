"""Allow an advance booking to hold one or two complete hours."""

from datetime import timedelta

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0015_create_cache_table")]

    operations = [
        migrations.RemoveConstraint(
            model_name="booking",
            name="booking_slot_length_one_hour",
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(slot_end=models.F("slot_start") + timedelta(hours=1))
                    | models.Q(slot_end=models.F("slot_start") + timedelta(hours=2))
                ),
                name="booking_slot_length_one_or_two_hours",
            ),
        ),
    ]
