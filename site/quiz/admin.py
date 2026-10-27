from django.contrib import admin
from .models import (Profile, CareRelationship, Invitation, SurveySession, AnswerRevision,
                     Questionnaire, QuestionnaireVersion, SurveyAssignment, ContentVersion,
                     PrescriptionVersion, MedicationIntake, CareMessage, CareNote,
                     AdverseEvent, ClinicalAction, Assessment, Alert, AuditEvent)

# The operational moderator uses audited views. Django admin is technical-superuser only.
admin.site.has_permission = lambda request: bool(request.user.is_active and request.user.is_staff and request.user.is_superuser)
admin.site.site_header = "Клинический компас — администрирование"
admin.site.site_title = "Администрирование Компаса"
admin.site.index_title = "Пользователи и данные приложения"
admin.site.index_template = "quiz/admin_index.html"
admin.site.site_url = "/"


class ReadOnlyAdmin(admin.ModelAdmin):
    """Inspect records without bypassing confirmed, versioned domain operations."""
    actions = None
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Profile)
class ProfileAdmin(ReadOnlyAdmin):
    list_display = ("user", "role", "verification_status", "moderator_enabled")
    list_filter = ("role", "verification_status", "moderator_enabled")
    search_fields = ("user__username", "user__first_name", "user__last_name")
    list_select_related = ("user",)
    exclude = ("doctors",)


@admin.register(CareRelationship)
class RelationshipAdmin(ReadOnlyAdmin):
    list_display = ("patient", "doctor", "status", "is_primary", "started_at", "ended_at")
    list_filter = ("status", "is_primary")
    search_fields = ("patient__username", "doctor__username")
    list_select_related = ("patient", "doctor")


@admin.register(Invitation)
class InvitationAdmin(ReadOnlyAdmin):
    list_display = ("id", "role", "issuer", "expires_at", "used_by", "used_at", "revoked_at")
    list_filter = ("role",)
    search_fields = ("issuer__username", "used_by__username")
    list_select_related = ("issuer", "used_by")
    exclude = ("token_digest",)


@admin.register(SurveySession)
class SessionAdmin(ReadOnlyAdmin):
    list_display = ("id", "patient", "status", "started_at", "completed_at", "answer_generation")
    list_filter = ("status", "started_at")
    search_fields = ("patient__username",)
    list_select_related = ("patient",)
    ordering = ("-started_at",)


@admin.register(AnswerRevision)
class AnswerAdmin(ReadOnlyAdmin):
    list_display = ("id", "session", "question_code", "revision", "state", "received_at")
    list_filter = ("state",)
    search_fields = ("session__patient__username", "question_code")
    ordering = ("-received_at",)


@admin.register(PrescriptionVersion)
class PrescriptionAdmin(ReadOnlyAdmin):
    list_display = ("id", "patient", "version", "created_at")
    search_fields = ("patient__username",)
    list_select_related = ("patient",)
    ordering = ("-created_at",)


@admin.register(MedicationIntake)
class IntakeAdmin(ReadOnlyAdmin):
    list_display = ("id", "prescription", "medication_id", "due_at", "status", "updated_at")
    list_filter = ("status", "due_at")
    search_fields = ("prescription__patient__username",)
    ordering = ("-due_at",)


@admin.register(CareMessage, CareNote)
class CorrespondenceAdmin(ReadOnlyAdmin):
    list_display = ("id", "patient", "author", "created_at")
    search_fields = ("patient__username", "author__username")
    list_select_related = ("patient", "author")
    ordering = ("-created_at",)


@admin.register(AdverseEvent)
class AdverseAdmin(ReadOnlyAdmin):
    list_display = ("id", "patient", "medicine", "created_at")
    search_fields = ("patient__username", "medicine")
    list_select_related = ("patient",)
    ordering = ("-created_at",)


@admin.register(ClinicalAction)
class DecisionAdmin(ReadOnlyAdmin):
    list_display = ("id", "patient", "clinician", "kind", "decision", "created_at")
    list_filter = ("kind", "decision")
    search_fields = ("patient__username", "clinician__username")
    list_select_related = ("patient", "clinician")
    ordering = ("-created_at",)


@admin.register(AuditEvent)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ("id", "actor", "kind", "subject_id", "created_at")
    list_filter = ("kind",)
    search_fields = ("actor__username",)
    list_select_related = ("actor",)
    ordering = ("-created_at",)


admin.site.register([Questionnaire, QuestionnaireVersion, SurveyAssignment, ContentVersion,
                     Assessment, Alert], ReadOnlyAdmin)
