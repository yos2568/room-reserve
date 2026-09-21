from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0012_room_availability_only")]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="recipient",
            field=models.ForeignKey(
                to="core.user", on_delete=django.db.models.deletion.PROTECT,
                related_name="notifications", null=True, blank=True,
            ),
        ),
        migrations.AddField(
            model_name="notification", name="recipient_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
    ]
