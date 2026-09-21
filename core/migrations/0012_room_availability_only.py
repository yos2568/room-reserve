from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_priority_three_workflows"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="availability_only",
            field=models.BooleanField(
                default=False,
                help_text="Show availability but do not allow reservations or walk-ins.",
            ),
        ),
    ]
