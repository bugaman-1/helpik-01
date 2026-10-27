from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from quiz.services.diary import backup_database, prepare


class Command(BaseCommand):
    help = "Back up the local database and prepare the 17-question author diary; assigns nobody."

    def handle(self, *args, **options):
        if not settings.DEMO_SURVEY_ENABLED:
            raise CommandError("Use the existing demo/test environment")
        backup = backup_database()
        version = prepare()
        self.stdout.write(f"Backup: {backup}\nDiary ready: {version.questionnaire.title}, version {version.number}, 17 questions. No assignments created.")
