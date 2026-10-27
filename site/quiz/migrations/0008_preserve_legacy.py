from django.db import migrations


def preserve_legacy(apps, schema_editor):
    Profile = apps.get_model("quiz", "Profile")
    Care = apps.get_model("quiz", "CareRelationship")
    Survey = apps.get_model("quiz", "SurveyRecord")
    alias = schema_editor.connection.alias
    # Keep original M2M rows unchanged. Unknown chronology and lead are not invented.
    for profile in Profile.objects.using(alias).all().iterator():
        for doctor_id in profile.doctors.using(alias).values_list("id", flat=True):
            Care.objects.using(alias).get_or_create(
                patient_id=profile.user_id, doctor_id=doctor_id, status="active",
                defaults={"started_at": None, "is_primary": False, "origin": "legacy_m2m", "reason": "Imported existing link; chronology and responsible clinician not established"},
            )
    Survey.objects.using(alias).update(origin="legacy_aggregate")
    # All old doctors remain unverified; old moderator credentials must be reset explicitly.


class Migration(migrations.Migration):
    dependencies = [("quiz", "0007_foundation")]
    operations = [migrations.RunPython(preserve_legacy)]
