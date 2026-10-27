"""Explicit synthetic provisioning; never a clinical publication path."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from quiz.models import Profile, Questionnaire, QuestionnaireVersion, SurveyAssignment
from quiz.services.questionnaires import create_version, _assign


def demo_definition():
    def question(code, text, *, safety=False, condition=None, optional=False):
        return {"code": code, "text": text, "required": not optional,
                "options": [{"code": "no", "label": "Нет (синтетический пример)"}, {"code": "yes", "label": "Да (синтетический пример)"}],
                "allow_not_applicable": condition is not None, "applicable_when": condition, "safety_relevant": safety}
    return {"schema": "survey-v2", "instruction": "Проверьте сохранение на вымышленном сценарии. Это технический набор, не медицинский опросник.",
        "recall_window": "Указанный демонстрационный календарный день; два прохождения не являются независимыми днями наблюдения.",
        "source": "Синтетический набор B03, редакция 1. Не PHQ-9, не GAD-7 и не авторский дневник опрос-1.",
        "completeness": "all_required", "questions": [
            question("demo.context", "Есть ли в вымышленном сценарии дополнительное событие?"),
            question("demo.detail", "Отмечена ли дополнительная деталь вымышленного события?", condition={"question": "demo.context", "option": "yes"}),
            question("demo.safety", "Содержит ли вымышленный сценарий критический факт для технической проверки очереди?", safety=True),
            question("demo.note", "Хотите добавить необязательную демонстрационную отметку?", optional=True),
        ]}


class Command(BaseCommand):
    help = "Create only synthetic B03 accounts and morning/evening demo windows for a date."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD in Europe/Moscow; defaults to today")

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.APP_ENV not in {"demo", "test"}:
            raise CommandError("Demo/test environment required")
        zone = ZoneInfo("Europe/Moscow")
        day = datetime.strptime(options["date"], "%Y-%m-%d").date() if options["date"] else timezone.now().astimezone(zone).date()
        User = get_user_model()
        if User.objects.exclude(username__startswith="synthetic_").exists():
            raise CommandError("Use an isolated database containing only synthetic accounts")
        users = []
        for name, role in [("synthetic_b03_editor", "moderator"), ("synthetic_b03_patient", "patient")]:
            account, created = User.objects.get_or_create(username=name)
            if created:
                account.set_unusable_password()
                account.save()
                Profile.objects.create(user=account, role=role, moderator_enabled=role == "moderator")
            elif not Profile.objects.filter(user=account, role=role).exists():
                raise CommandError("Synthetic account name collision; no changes applied")
            users.append(account)
        editor, patient = users
        questionnaire, _ = Questionnaire.objects.get_or_create(code="b03-technical-demo", defaults={"title": "Техническая проверка опроса B03"})
        version = QuestionnaireVersion.objects.filter(questionnaire=questionnaire, status="demo").order_by("number").first()
        if version is None:
            version = create_version(editor, questionnaire, demo_definition(), status="demo")
        start = datetime.combine(day, time.min, zone)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        noon = datetime.combine(day, time(12), zone)
        for label, left, right in [("morning", start, noon), ("evening", noon, end)]:
            _assign(editor, patient, version, key=uuid.uuid5(uuid.NAMESPACE_URL, f"b03:{day}:{label}"),
                timezone_name=zone.key, available_from=left, available_until=right,
                observation_start=start, observation_end=end, window_code=label, unscheduled_limit=1)
        self.stdout.write("Synthetic B03 windows created/reused. Patient: synthetic_b03_patient. No password printed or changed; use changepassword explicitly if needed.")
