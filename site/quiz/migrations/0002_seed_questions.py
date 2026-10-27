from django.db import migrations


def create_questions(apps, schema_editor):
    Question = apps.get_model("quiz", "Question")
    questions = [
        "Как тебе сегодняшняя погода?",
        "Как прошло утро?",
        "Как настроение сейчас?",
        "Как дела на работе или учёбе?",
        "Как оцениваешь этот день в целом?",
    ]
    Question.objects.bulk_create(
        Question(text=text, order=i + 1) for i, text in enumerate(questions)
    )


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_questions, migrations.RunPython.noop),
    ]