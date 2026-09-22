"""Allow two dated meetings of the same weekday and hour.

Room 304's Counterpoint class meets on two Mondays only. The old unique
constraint on (room, weekday, start_hour) could not store both.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0013_notification_external_recipient")]

    operations = [
        migrations.RemoveConstraint(
            model_name="weeklyblock",
            name="weekly_block_room_day_hour_unique",
        ),
        migrations.AddConstraint(
            model_name="weeklyblock",
            constraint=models.UniqueConstraint(
                fields=("room", "weekday", "start_hour"),
                condition=models.Q(valid_from__isnull=True, valid_until__isnull=True),
                name="weekly_block_open_ended_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="weeklyblock",
            constraint=models.UniqueConstraint(
                fields=("room", "weekday", "start_hour", "valid_from", "valid_until"),
                condition=models.Q(valid_from__isnull=False, valid_until__isnull=False),
                name="weekly_block_dated_unique",
            ),
        ),
    ]
