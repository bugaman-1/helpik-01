from django.db import migrations


def backfill_profiles(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("quiz", "Profile")
    for user in User.objects.all():
        Profile.objects.get_or_create(
            user=user,
            defaults={"role": "patient"},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0004_profile"),
    ]

    operations = [
        migrations.RunPython(backfill_profiles, migrations.RunPython.noop),
    ]