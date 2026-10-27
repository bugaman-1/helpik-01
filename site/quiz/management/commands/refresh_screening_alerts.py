from django.core.management.base import BaseCommand
from quiz.models import CareRelationship
from quiz.services.alerts import sync_patient


class Command(BaseCommand):
    help = 'Evaluate saved screening answers once using idempotent prototype rules.'

    def handle(self, *args, **options):
        for patient_id in CareRelationship.objects.filter(status='active').values_list('patient_id', flat=True).distinct():
            sync_patient(patient_id)
        self.stdout.write('Screening alerts refreshed; no clinical actions performed.')
