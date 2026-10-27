from django import forms
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from quiz.ui_views import shell


class PreferencesForm(forms.Form):
    theme = forms.ChoiceField(label="Оформление", choices=[("light", "Светлое"), ("dark", "Тёмное"), ("system", "Как на устройстве")])
    reminders = forms.BooleanField(label="Напоминания на главном экране", required=False)
    alerts_only = forms.BooleanField(label="По умолчанию показывать только пациентов с сигналами", required=False)
    email = forms.EmailField(label="Почта для связи", required=False)
    contact_phone = forms.RegexField(r"^\+?[0-9 ()-]{5,40}$", label="Телефон для связи", required=False, error_messages={"invalid": "Укажите номер телефона: цифры, пробелы, +, скобки или дефис."})
    contact_address = forms.CharField(label="Адрес приёма", max_length=300, required=False)

    def __init__(self, *args, doctor=False, **kwargs):
        super().__init__(*args, **kwargs)
        if doctor:
            self.fields.pop("reminders")
        else:
            for key in ("alerts_only", "email", "contact_phone", "contact_address"):
                self.fields.pop(key)


class CloseAccountForm(forms.Form):
    password = forms.CharField(label="Текущий пароль", widget=forms.PasswordInput)
    confirm = forms.BooleanField(label="Я понимаю, что доступ к аккаунту будет отключён")


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    profile = request.user.profile
    doctor = profile.role == "doctor"
    initial = {"theme": "light", "reminders": True, "alerts_only": False, **profile.preferences,
               "email": request.user.email, "contact_phone": profile.contact_phone, "contact_address": profile.contact_address}
    form = PreferencesForm(request.POST if request.method == "POST" and request.POST.get("action") == "save" else None, initial=initial, doctor=doctor)
    close_form = CloseAccountForm(request.POST if request.method == "POST" and request.POST.get("action") == "close" else None)
    if request.method == "POST":
        if request.POST.get("action") == "save" and form.is_valid():
            data = form.cleaned_data
            profile.preferences = {**profile.preferences, **{key: data[key] for key in ("theme", "reminders", "alerts_only") if key in data}}
            if doctor:
                profile.contact_phone, profile.contact_address = data["contact_phone"], data["contact_address"]
                request.user.email = data["email"]
                request.user.save(update_fields=["email"])
            profile.save(update_fields=["preferences", "contact_phone", "contact_address"])
            messages.success(request, "Настройки сохранены.")
            return redirect("settings")
        if request.POST.get("action") == "close" and close_form.is_valid():
            if not request.user.check_password(close_form.cleaned_data["password"]):
                close_form.add_error("password", "Пароль не подошёл.")
            else:
                request.user.is_active = False
                request.user.save(update_fields=["is_active"])
                logout(request)
                messages.success(request, "Аккаунт закрыт. Для восстановления доступа обратитесь к модератору.")
                return redirect("login")
    return render(request, "quiz/ui/settings.html", {**shell("settings", role=profile.role), "preferences_form": form, "close_form": close_form})
