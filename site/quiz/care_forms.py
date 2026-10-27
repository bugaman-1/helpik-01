from datetime import time
from django import forms
from quiz.models import QuestionnaireVersion


class KeyForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput)


class NoteForm(KeyForm):
    text = forms.CharField(label="Заметка", max_length=4000, widget=forms.Textarea)


class MessageForm(NoteForm):
    field_order = ["key", "kind", "text"]
    kind = forms.ChoiceField(label="Тип", choices=[("message", "Сообщение"), ("contact", "Запрос контакта")])
    text = forms.CharField(label="Текст", max_length=4000, widget=forms.Textarea)


class AdverseForm(NoteForm):
    field_order = ["key", "medicine", "text"]
    medicine = forms.CharField(label="Название лекарства (если известно)", max_length=200, required=False)
    text = forms.CharField(label="Что произошло и когда", max_length=4000, widget=forms.Textarea)


class PlanForm(KeyForm):
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    reason = forms.CharField(label="План / решение и основание", max_length=4000, widget=forms.Textarea)
    confirm = forms.BooleanField(label="Подтверждаю решение врача")


class PrescriptionForm(PlanForm):
    field_order = ["key", "expected_version", "medication_id", "name", "dose", "unit", "times", "start", "end", "note", "interaction_token", "interaction_ack", "confirm"]
    medication_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(label="Название", max_length=200)
    dose = forms.DecimalField(label="Доза", min_value=0.0001, max_value=1000000, max_digits=12, decimal_places=4)
    unit = forms.CharField(label="Единица дозы", max_length=40)
    times = forms.CharField(label="Время каждый день (ЧЧ:ММ, через запятую)", max_length=100)
    start = forms.DateField(label="С даты", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    end = forms.DateField(label="По дату включительно", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    note = forms.CharField(label="Примечание", max_length=2000, required=False, widget=forms.Textarea)
    reason = None
    confirm = forms.BooleanField(label="Подтверждаю назначение")
    interaction_token = forms.CharField(required=False, widget=forms.HiddenInput)
    interaction_ack = forms.BooleanField(required=False, label="Учёл предупреждения о сочетаниях")

    def clean_times(self):
        try:
            values = self.cleaned_data["times"].split(",")
            result = sorted({time.fromisoformat(v.strip()).strftime("%H:%M") for v in values})
            if not 1 <= len(result) <= 12 or any(len(v.strip()) != 5 for v in values):
                raise ValueError()
            return result
        except ValueError:
            raise forms.ValidationError("Укажите до 12 значений в формате ЧЧ:ММ через запятую.")

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["end"] < data["start"]:
            self.add_error("end", "Окончание раньше начала.")
        # The audit stores the action, without inventing a clinical rationale.
        data['reason'] = 'Изменение назначения' if data.get('medication_id') else 'Назначение лекарства'
        return data


class ReportedMedicationForm(KeyForm):
    entry_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    name = forms.CharField(label="Название лекарства", max_length=200)
    regimen = forms.CharField(label="Доза и режим приёма", max_length=500, required=False)
    start = forms.DateField(label="Начало приёма (если известно)", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    end = forms.DateField(label="Последний день приёма (если известен)", required=False, widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    note = forms.CharField(label="Примечание", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            self.add_error("end", "Окончание раньше начала приёма.")
        from django.utils import timezone
        data['status'] = 'stopped' if data.get('end') and data['end'] < timezone.localdate() else 'taking'
        data['source'] = 'doctor'
        return data


class InteractionCheckForm(forms.Form):
    name = forms.CharField(max_length=200, required=False)
    start = forms.DateField()
    end = forms.DateField()
    exclude = forms.UUIDField(required=False)

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            self.add_error("end", "Окончание раньше начала.")
        return data


class AssignmentForm(KeyForm):
    version = forms.ModelChoiceField(label="Опрос", queryset=QuestionnaireVersion.objects.none())
    day = forms.DateField(label="День наблюдения", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    extra = forms.IntegerField(label="Дополнительных прохождений за день", min_value=0, max_value=10, initial=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from quiz.services.diary import CODE
        from quiz.services.checkin import CODE as SCREENING_CODE
        self.fields["version"].queryset = QuestionnaireVersion.objects.filter(status="demo", questionnaire__code__in=[CODE, SCREENING_CODE]).select_related("questionnaire").order_by("-number")
        self.fields["version"].label_from_instance = lambda v: v.questionnaire.title


class IntakeForm(forms.Form):
    prescription = forms.IntegerField(min_value=1)
    medication_id = forms.UUIDField()
    due_at = forms.DateTimeField()
    status = forms.ChoiceField(choices=[("taken", "Принято"), ("skipped", "Пропущено")])
