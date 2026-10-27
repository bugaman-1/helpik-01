import time
from functools import wraps
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, OperationalError
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from django.utils.decorators import method_decorator
from quiz.forms import RegisterForm, DoctorRegistrationForm, PatientRegistrationForm, AttachPatientForm
from quiz.models import Invitation, AuditEvent, Profile
from quiz.services import invitations, care, onboarding
from quiz.services.access import is_moderator, is_approved_doctor, has_role
from quiz.services.contracts import DomainError
from quiz.services.demo_invitation import local_demo_token

ERRORS = {"unsupported_plan": (409, "Предыдущая схема сохранена в другом формате. Автоматическая замена недоступна."), "stale_version": (409, "Схема уже изменена другим врачом или в другой вкладке. Откройте карточку и проверьте новую версию перед подтверждением."), "idempotency_conflict": (409, "Эта отправка уже сохранена с другим содержимым. Откройте карточку и проверьте запись."), "forbidden": (403, "Доступ запрещён."), "replacement_required": (409, "Сначала назначьте другого допущенного ответственного врача и передайте открытые сигналы."), "reason_required": (400, "Укажите основание изменения (до 500 символов), без медицинских подробностей."), "invalid_input": (400, "Проверьте введённые значения."), "clinical_publication_disabled": (409, "Клинический модуль ещё не опубликован.")}
GENERIC = "Не удалось выполнить действие. Проверьте данные или обратитесь к выдавшему приглашение."
ERRORS['stale_medication'] = (409, 'Сведения об этом лекарстве уже изменились. Откройте карточку пациента и проверьте последнюю запись перед исправлением.')


def csrf_failure(request, reason=""):
    return render(request, "quiz/action_error.html", {"message": "Не удалось отправить форму. Обновите страницу и повторите попытку."}, status=403)


def domain_view(fn):
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except DomainError as exc:
            status, message = ERRORS.get(exc.code, (400, GENERIC))
            return render(request, "quiz/action_error.html", {"message": message}, status=status)
        except (ValueError, TypeError, ValidationError, OverflowError):
            return render(request, "quiz/action_error.html", {"message": "Проверьте введённые значения."}, status=400)
        except OperationalError:
            response = render(request, "quiz/action_error.html", {"message": "Сохранение временно недоступно. Повторите запрос."}, status=503)
            response["Retry-After"] = "2"
            return response
    return wrapped


def allowed_attempt(request, scope):
    return invitations.throttle(scope, request.META.get("REMOTE_ADDR", "unknown"))


def context_digest(request):
    context = request.session.get("invitation_context", {})
    if context.get("until", 0) <= time.time():
        raise DomainError("invitation_unavailable")
    return context.get("digest", "")


class ThrottledLoginView(LoginView):
    template_name = "registration/login.html"

    @method_decorator(sensitive_post_parameters())
    def post(self, request, *args, **kwargs):
        if not allowed_attempt(request, "login"):
            return HttpResponse("Слишком много попыток. Повторите позднее.", status=429)
        return super().post(request, *args, **kwargs)


@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
@domain_view
def invitation_exchange(request):
    error = None
    selected_role = "patient"
    if request.method == "POST":
        if not allowed_attempt(request, "exchange"):
            return HttpResponse("Слишком много попыток. Повторите позднее.", status=429)
        request.session.pop("invitation_context", None)
        try:
            digest = invitations.exchange(request.POST.get("token", "").strip())
            request.session.cycle_key()
            request.session["invitation_context"] = {"digest": digest, "until": time.time() + 600}
            return redirect("accept_invitation" if request.user.is_authenticated else "register")
        except DomainError as exc:
            error = "Код выдан для другой роли. Выберите соответствующую роль или используйте другой код." if exc.code == "role_mismatch" else "Код не найден, уже использован или истёк. Проверьте код целиком либо запросите новое приглашение."
    from .services.demo_invitation import local_ui_demo_tokens
    demo_tokens = local_ui_demo_tokens(request)
    suggested_token = demo_tokens.get(selected_role) or (local_demo_token(request) if selected_role == "patient" else None)
    return render(request, "quiz/invitation_exchange.html", {"error": error, "selected_role": selected_role,
        "demo_tokens": demo_tokens, "demo_token": suggested_token if request.method == "GET" else None})


@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
@domain_view
def register(request):
    if request.user.is_authenticated:
        return redirect("accept_invitation")
    try:
        digest = context_digest(request)
    except DomainError:
        return redirect("invitation_exchange")
    form = RegisterForm(request.POST if request.method == "POST" else None)
    error = None
    if request.method == "POST":
        if not allowed_attempt(request, "register"):
            return HttpResponse("Слишком много попыток. Повторите позднее.", status=429)
        if form.is_valid():
            candidate = form.save(commit=False)  # Password hashing outside the write transaction.
            try:
                user = invitations.register_from_context(digest, candidate)
                request.session.pop("invitation_context", None)
                login(request, user)
                return redirect("home")
            except (DomainError, IntegrityError):
                pass
        else:
            # Keep expensive password work for both duplicate and new usernames.
            make_password(request.POST.get("password1", "")[:4096])
        error = GENERIC  # Same message for duplicate username and every validation failure.
        form = RegisterForm()  # No field-level account-existence disclosure or password echo.
    invitation = invitations._available(digest)
    return render(request, "quiz/register.html", {"form": form, "error": error, "invitation_role": invitation.role})


@sensitive_post_parameters()
@require_http_methods(['GET', 'POST'])
@domain_view
def register_patient(request):
    onboarding.require_enabled()
    if request.user.is_authenticated:
        return redirect('home')
    form = PatientRegistrationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        if not allowed_attempt(request, 'patient-register'):
            return HttpResponse('Слишком много попыток. Повторите позднее.', status=429)
        if form.is_valid():
            try:
                user = onboarding.register_patient(form.save(commit=False))
            except IntegrityError:
                form.add_error('username', 'Этот логин уже занят.')
            else:
                request.session.pop('invitation_context', None)
                login(request, user)
                return redirect('home')
    return render(request, 'quiz/register_patient.html', {'form': form})


@sensitive_post_parameters()
@require_http_methods(['GET', 'POST'])
@domain_view
def register_doctor(request):
    onboarding.require_enabled()
    if request.user.is_authenticated:
        return redirect('home')
    form = DoctorRegistrationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        if not allowed_attempt(request, 'doctor-register'):
            return HttpResponse('Слишком много попыток. Повторите позднее.', status=429)
        if form.is_valid():
            try:
                user = onboarding.register_doctor(form.save(commit=False))
            except IntegrityError:
                form.add_error('username', 'Этот логин уже занят.')
            else:
                request.session.pop('invitation_context', None)
                login(request, user)
                return redirect('home')
    return render(request, 'quiz/register_doctor.html', {'form': form})


@login_required
@require_http_methods(['GET', 'POST'])
@domain_view
def add_patient(request):
    onboarding.require_enabled()
    if not is_approved_doctor(request.user):
        raise DomainError('forbidden')
    form = AttachPatientForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        if not invitations.throttle('attach-patient', str(request.user.pk), limit=30, seconds=3600):
            return HttpResponse('Слишком много попыток. Повторите позднее.', status=429)
        if form.is_valid():
            try:
                relationship = onboarding.attach_patient(request.user, form.cleaned_data['username'])
            except DomainError as exc:
                if exc.code != 'patient_not_found':
                    raise
                form.add_error('username', 'Активный пациент с таким логином не найден.')
            else:
                messages.success(request, 'Пациент прикреплён.')
                return redirect('patient_records', pk=relationship.patient_id)
    return render(request, 'quiz/ui/add_patient.html', {'form': form, 'ui_role': 'doctor'})


@login_required
@require_http_methods(["GET", "POST"])
@domain_view
def accept_invitation(request):
    if not has_role(request.user, "patient"):
        raise DomainError("forbidden")
    digest = context_digest(request)
    invitation = invitations._available(digest)
    if invitation.role != "patient":
        raise DomainError("invitation_unavailable")
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            raise DomainError("invalid_input")
        invitations.accept_for_patient(request.user, digest)
        request.session.pop("invitation_context", None)
        messages.success(request, "Новая связь с врачом подтверждена.")
        return redirect("home")
    return render(request, "quiz/accept_invitation.html", {"doctor": invitation.issuer})


@login_required
@require_http_methods(["GET", "POST"])
@domain_view
def invitation_list(request):
    if is_moderator(request.user):
        return redirect("create_doctor")
    if not is_approved_doctor(request.user):
        raise DomainError("forbidden")
    issued = Invitation.objects.filter(issuer=request.user, role="patient").order_by("-created_at")[:100]
    context = {"invitations": issued}
    if request.method == "POST":
        if not invitations.throttle("issue", str(request.user.pk), limit=30, seconds=3600):
            return HttpResponse("Лимит выдачи приглашений. Повторите позднее.", status=429)
        invitation, token = invitations.issue(request.user, role="patient")
        context.update(token=token, invitation=invitation, invite_link=request.build_absolute_uri("/invite/") + "#" + token)
    return render(request, "quiz/invitations.html", context)


@login_required
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
@domain_view
def create_doctor(request):
    if not is_moderator(request.user):
        raise DomainError("forbidden")
    form = RegisterForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                doctor = form.save(commit=False)
                doctor.save(force_insert=True)
                Profile.objects.create(user=doctor, role="doctor", verification_status="approved")
                care.audit(request.user, "doctor.created", doctor.pk, "Аккаунт врача создан модератором")
        except IntegrityError:
            form.add_error("username", "Логин уже занят.")
        else:
            messages.success(request, "Аккаунт врача создан. Передайте ему логин и пароль.")
            return redirect("home")
    return render(request, "quiz/create_doctor.html", {"form": form, "ui_role": "moderator"})


@login_required
@require_POST
@domain_view
def revoke_invitation(request, pk):
    invitations.revoke(request.user, pk, request.POST.get("reason", ""))
    return redirect("invitations")


@login_required
@require_POST
@domain_view
def attach_doctor(request, patient_pk):
    care.attach(request.user, patient_pk, int(request.POST.get("doctor_id", "")), request.POST.get("reason", ""))
    return redirect("home")


@login_required
@require_POST
@domain_view
def detach_doctor(request, patient_pk, doctor_pk):
    replacement = request.POST.get("replacement_id")
    care.detach(request.user, patient_pk, doctor_pk, request.POST.get("reason", ""), int(replacement) if replacement else None, request.POST.get("end_care") == "yes")
    return redirect("home")


@login_required
@require_POST
@domain_view
def transfer_responsibility(request, patient_pk):
    care.transfer(request.user, patient_pk, int(request.POST.get("doctor_id", "")), request.POST.get("reason", ""))
    return redirect("home")


@login_required
@require_POST
@domain_view
def verify_doctor(request, pk):
    care.verify_doctor(request.user, pk, request.POST.get("status", ""), request.POST.get("reason", ""))
    return redirect("home")


@login_required
@domain_view
def audit_log(request):
    if not is_moderator(request.user):
        raise DomainError("forbidden")
    return render(request, "quiz/audit.html", {"events": AuditEvent.objects.order_by("-created_at")[:200]})
