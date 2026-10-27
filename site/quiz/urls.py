from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from . import views, access_views
from . import survey_views
from . import ui_views, care_views
from . import preferences
from . import notification_views
from . import medication_views

urlpatterns = [
    path('register/patient/', access_views.register_patient, name='register_patient'),
    path('register/doctor/', access_views.register_doctor, name='register_doctor'),
    path('doctor/patients/add/', access_views.add_patient, name='add_patient'),
    path('care/<int:pk>/medications/', medication_views.save, name='care_medication_save'),
    path('care/<int:pk>/medications/check/', medication_views.check, name='care_medication_check'),
    path('doctor/drug-sources/preparaty/', medication_views.source_document, name='drug_source_document'),
    path("notifications/feed/", notification_views.feed, name="notification_feed"),
    path("notifications/subscription/", notification_views.subscription, name="push_subscription"),
    path("alerts/<int:pk>/review/", notification_views.resolve, name="alert_review"),
    path("notifications-worker.js", notification_views.worker, name="notification_worker"),
    path("app.webmanifest", notification_views.manifest, name="manifest"),
    path("moderator/doctors/new/", access_views.create_doctor, name="create_doctor"),
    path("settings/", preferences.settings_view, name="settings"),
    path("surveys/screening/start/", survey_views.screening_start, name="screening_start"),
    path("care/<int:pk>/message/", care_views.message, name="care_message"),
    path("care/<int:pk>/note/", care_views.note, name="care_note"),
    path("care/<int:pk>/plan/", care_views.action, {"kind": "plan"}, name="care_plan"),
    path("care/<int:pk>/prescribe/", care_views.action, {"kind": "prescription"}, name="care_prescribe"),
    path("care/<int:pk>/stop/<uuid:medication_id>/", care_views.action, {"kind": "stop"}, name="care_stop"),
    path("care/<int:pk>/assign/", care_views.assign, name="care_assign"),
    path("medicines/intake/", care_views.intake, name="care_intake"),
    path("medicines/adverse/", care_views.adverse, name="care_adverse"),
    path("moderator/prepare-survey/", care_views.prepare_demo_survey, name="care_prepare_survey"),
    path("medicines/", ui_views.medicines, name="medicines"),
    path("contact/", ui_views.contact, name="contact"),
    path("demo/ui/<str:screen>/", ui_views.preview, name="ui_preview"),
    path("surveys/", survey_views.index, name="surveys"),
    path("surveys/assignments/<int:assignment_id>/start/", survey_views.start, name="survey_assignment_start"),
    path("surveys/<uuid:session_id>/", survey_views.detail, name="survey_detail"),
    path("surveys/<uuid:session_id>/answer/", survey_views.answer, name="survey_answer"),
    path("surveys/<uuid:session_id>/finish/", survey_views.finish, name="survey_finish"),
    path("surveys/<uuid:session_id>/consent/", survey_views.telemetry_consent, name="survey_consent"),
    path("surveys/<uuid:session_id>/events/", survey_views.events, name="survey_events"),
    path("demo/setup/", RedirectView.as_view(pattern_name="login", permanent=False, http_method_names=["get", "head", "options"])),
    path("invite/", access_views.invitation_exchange, name="invitation_exchange"),
    path("invite/accept/", access_views.accept_invitation, name="accept_invitation"),
    path("invitations/", access_views.invitation_list, name="invitations"),
    path("invitations/<int:pk>/revoke/", access_views.revoke_invitation, name="revoke_invitation"),
    path("moderator/doctors/<int:pk>/verify/", access_views.verify_doctor, name="verify_doctor"),
    path("moderator/patients/<int:patient_pk>/responsibility/", access_views.transfer_responsibility, name="transfer_responsibility"),
    path("moderator/audit/", access_views.audit_log, name="audit_log"),
    path("survey/start/", views.start_survey, name="start_survey"),
    path("", ui_views.home, name="home"),
    path("register/", access_views.register, name="register"),
    path(
        "login/",
        access_views.ThrottledLoginView.as_view(),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("doctor/patients/<int:pk>/", ui_views.patient_card, name="patient_records"),
    path("activity/", ui_views.activity, name="activity"),
    path("moderator/patients/<int:patient_pk>/attach/", access_views.attach_doctor, name="attach_doctor"),
    path("moderator/patients/<int:patient_pk>/detach/<int:doctor_pk>/", access_views.detach_doctor, name="detach_doctor"),
    path("q/<int:pk>/", views.question, name="question"),
    path("stats/", views.stats, name="stats"),
]
