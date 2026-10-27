from django.core.management.base import BaseCommand
from quiz.services.checkin import prepare


class Command(BaseCommand):
    help = "Prepare the daily опрос2 prototype, retaining all previous questionnaires."

    def handle(self, *args, **options):
        version = prepare()
        self.stdout.write(f"Опрос готов: {version.questionnaire.title}, редакция {version.number}.")
