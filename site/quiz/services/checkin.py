"""Daily editorial questionnaire from опрос2.txt; not a validated short BDI.

The state-anxiety reverse key gives 20..80 (direct - reverse + 50).
No trait-anxiety items were supplied, so no trait score is calculated.
"""
import uuid
from datetime import datetime, time, timedelta
from statistics import median
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from quiz.models import Profile, Questionnaire, QuestionnaireVersion, ContentVersion, SurveySession
from . import questionnaires as engine
from .contracts import DomainError

CODE = 'daily-checkin-opros2'
TITLE = 'Самочувствие сегодня'
CONTROL_PAIR = 'STAI03'
CONTROL_CODE = 'CHECK01'
REVERSE = {1, 2, 5, 8, 10, 11, 15, 16, 19, 20}
SOURCE = 'опрос2.txt: 5 выбранных пунктов BDI с периодом «сегодня» и 20 пунктов реактивной тревожности Спилбергера—Ханина. Редакционная версия прототипа; не валидированная сокращённая шкала BDI.'
SCORING_SOURCE = 'https://iaap-journals.onlinelibrary.wiley.com/doi/10.1111/j.1758-0854.2009.01017.x'


def definition():
    mood = [
        ('Настроение', ['Я не чувствую себя расстроенным или печальным.', 'Я чувствую себя расстроенным.', 'Я всё время расстроен и не могу от этого отвлечься.', 'Я настолько расстроен и несчастен, что я не могу это выдержать.']),
        ('Мысли о будущем', ['Я не тревожусь о своём будущем.', 'Мысли о будущем меня озадачивают.', 'Мне кажется, что в будущем меня ничего не ждёт.', 'Моё будущее кажется безнадёжным, и ничто не может измениться к лучшему.']),
        ('Удовлетворение от жизни', ['Я получаю столько же удовлетворения от жизни, как раньше.', 'Я получаю меньше удовлетворения от жизни, чем раньше.', 'Я больше ни от чего не получаю удовлетворения.', 'Я полностью не удовлетворён жизнью, и мне всё надоело.']),
        ('Мысли о причинении себе вреда', ['Сегодня у меня не было мыслей покончить с собой.', 'У меня возникали мысли покончить с собой, но я не собираюсь их осуществлять.', 'Я хотел бы покончить с собой.', 'Я бы покончил с собой, если бы представилась возможность.']),
        ('Усталость', ['Я устаю не больше, чем обычно.', 'Я устаю быстрее, чем раньше.', 'Я устаю почти от всего, что делаю.', 'Из-за усталости я не могу ничего делать.']),
    ]
    anxiety = ['Я спокоен', 'Мне ничто не угрожает', 'Я нахожусь в напряжении', 'Я испытываю сожаление',
        'Я чувствую себя свободно', 'Я расстроен', 'Меня волнуют возможные неудачи', 'Я чувствую себя отдохнувшим',
        'Я не доволен собой', 'Я испытываю чувство внутреннего удовлетворения', 'Я уверен в себе', 'Я нервничаю',
        'Я не нахожу себе места', 'Я чувствую себя взвинченным', 'Я не чувствую скованности или напряжённости',
        'Я доволен', 'Я озабочен', 'Я слишком возбуждён, и мне не по себе', 'Мне радостно', 'Мне приятно']
    choices = ['Нет, это не так', 'Пожалуй, так', 'Верно', 'Совершенно верно']
    def question(code, text, labels):
        return {'code': code, 'text': text, 'required': True,
            'options': [{'code': f'{code}.{i}', 'label': label} for i, label in enumerate(labels)],
            'allow_not_applicable': False, 'applicable_when': None, 'safety_relevant': code == 'BDI04'}
    questions = [question(f'BDI{i:02}', text, labels) for i, (text, labels) in enumerate(mood, 1)]
    questions += [question(f'STAI{i:02}', text, choices) for i, text in enumerate(anxiety, 1)]
    questions.append(question(CONTROL_CODE, 'Я чувствую внутреннее напряжение', choices))
    return engine.validate_definition({'schema': 'survey-v2',
        'instruction': 'В первых пяти вопросах выберите утверждение, которое лучше описывает ваше состояние сегодня. Затем оцените, как вы чувствуете себя прямо сейчас. Здесь нет правильных или неправильных ответов.',
        'recall_window': 'Самочувствие — сегодня; тревога — прямо сейчас', 'source': SOURCE,
        'completeness': 'all_required', 'questions': questions})


@transaction.atomic
def prepare():
    if not settings.DEMO_SURVEY_ENABLED:
        raise DomainError('clinical_publication_disabled')
    questionnaire, _ = Questionnaire.objects.get_or_create(code=CODE, defaults={'title': TITLE})
    Questionnaire.objects.select_for_update().get(pk=questionnaire.pk)
    data = definition()
    versions = QuestionnaireVersion.objects.filter(questionnaire=questionnaire).select_related('content').order_by('-number')
    for version in versions:
        if version.content.definition == data and version.status == 'demo':
            return version
    latest = versions.first()
    number = latest.number + 1 if latest else 1
    content = ContentVersion.objects.create(code='survey-' + CODE, version=number, kind='questionnaire', definition=data, source_snapshot={'source': SOURCE})
    return QuestionnaireVersion.objects.create(questionnaire=questionnaire, content=content, number=number, status='demo')


@transaction.atomic
def start(patient):
    if not engine.has_role(patient, 'patient'):
        raise DomainError('forbidden')
    Profile.objects.select_for_update().get(user=patient)
    now = timezone.now()
    midnight = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    existing = SurveySession.objects.filter(patient=patient, questionnaire_version__questionnaire__code=CODE,
        started_at__gte=midnight, started_at__lt=midnight + timedelta(days=1), status__in=['draft', 'in_progress', 'submitted']).order_by('-started_at').first()
    if existing:
        return existing
    assignment = engine._assign(patient, patient, prepare(), key=uuid.uuid4(), timezone_name=settings.TIME_ZONE,
        available_from=now, available_until=midnight + timedelta(days=1), observation_start=midnight,
        observation_end=now, window_code='daily-checkin', unscheduled_limit=0)
    run = engine.start(patient, assignment.pk, uuid.uuid4())
    run.telemetry_enabled = True
    run.save(update_fields=['telemetry_enabled'])
    return run


def results(run):
    answers = engine.current_answers(run)
    def value(code):
        answer = answers.get(code)
        if answer and answer.state == 'known' and answer.value in {f'{code}.{i}' for i in range(4)}:
            return int(answer.value[-1])
        return None
    mood = [value(f'BDI{i:02}') for i in range(1, 6)]
    anxiety = [value(f'STAI{i:02}') for i in range(1, 21)]
    complete = run.status == 'submitted' and None not in mood + anxiety
    total = sum(mood) if complete else None
    state = sum(4 - v if i in REVERSE else v + 1 for i, v in enumerate(anxiety, 1)) if complete else None
    positive = list(run.revisions.filter(question_code='BDI04', state='known', value__in=['BDI04.1', 'BDI04.2', 'BDI04.3']).values_list('value', flat=True))
    safety_value = max((int(v[-1]) for v in positive), default=0)
    return {'kind': 'checkin', 'complete': complete, 'depression': total, 'anxiety': state,
        'trend_score': total - mood[3] if complete else None, 'safety': safety_value > 0, 'safety_value': safety_value,
        'anxiety_band': None if state is None else {'label': 'Высокая' if state >= 46 else 'Умеренная' if state >= 31 else 'Низкая', 'level': 'high' if state >= 46 else 'review' if state >= 31 else 'low'},
        'source': SCORING_SOURCE}


def change_from_baseline(run, score):
    """Prototype alert: +4/12 vs median of >=3 recent same-version assessments."""
    previous = SurveySession.objects.filter(patient_id=run.patient_id, questionnaire_version_id=run.questionnaire_version_id,
        status='submitted', completed_at__lt=run.started_at, started_at__gte=run.started_at - timedelta(days=7)).order_by('-completed_at')[:7]
    values = [results(old)['trend_score'] for old in previous]
    values = [v for v in values if v is not None]
    baseline = median(values) if len(values) >= 3 else None
    return {'baseline': baseline, 'delta': score - baseline if baseline is not None and score is not None else None}
