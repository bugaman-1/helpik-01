import secrets
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from quiz.models import Profile, CareRelationship, AuditEvent
from quiz.services import onboarding, invitations
from quiz.services.access import can_access_patient
from quiz.services.contracts import DomainError
from quiz.test_b03 import setup_case


@override_settings(PROTOTYPE_SELF_SERVICE_ENABLED=True, APP_ENV='test')
class PrototypeOnboardingTests(TestCase):
    def setUp(self):
        setup_case(self)
        self.password = secrets.token_urlsafe(24)

    def payload(self, **changes):
        return {'username':'prototype_new_doctor', 'first_name':'Тестовый', 'last_name':'Врач',
                'password1':self.password, 'password2':self.password, **changes}

    def test_self_registration_csrf_and_fixed_doctor_role_without_invitation(self):
        client = Client(enforce_csrf_checks=True)
        path = reverse('register_doctor')
        self.assertEqual(client.post(path, self.payload()).status_code, 403)
        self.assertContains(client.get(reverse('invitation_exchange')), 'Я врач')
        token = client.cookies['csrftoken'].value
        result = client.post(path, self.payload(role='moderator', is_staff='on', moderator_enabled='on', csrfmiddlewaretoken=token))
        self.assertEqual(result.status_code, 302)
        doctor = get_user_model().objects.get(username='prototype_new_doctor')
        self.assertTrue(doctor.check_password(self.password))
        self.assertEqual(doctor.profile.role, 'doctor')
        self.assertEqual(doctor.profile.verification_status, 'approved')
        self.assertFalse(doctor.is_staff or doctor.is_superuser or doctor.profile.moderator_enabled)
        self.assertContains(client.get('/'), 'Добавить пациента')
        self.assertFalse(can_access_patient(doctor, self.patient.pk))
        self.assertTrue(AuditEvent.objects.filter(actor=doctor, kind='doctor.self_registered').exists())
        invite, _ = invitations.issue(doctor, role='patient')
        self.assertEqual(invite.issuer_id, doctor.pk)

    def test_self_registration_keeps_password_validation_and_duplicate_account(self):
        path = reverse('register_doctor')
        self.client.post(path, self.payload(password1='123', password2='123'))
        self.assertFalse(get_user_model().objects.filter(username='prototype_new_doctor').exists())
        self.client.post(path, self.payload(username=self.doctor.username))
        self.assertEqual(Profile.objects.filter(user=self.doctor, role='doctor').count(), 1)
        self.assertFalse(self.client.session.get('_auth_user_id'))

    def test_exact_login_attachment_is_idempotent_and_preserves_primary_doctor(self):
        new = get_user_model()(username='prototype_attach_doctor')
        new.set_unusable_password()
        onboarding.register_doctor(new)
        self.client.force_login(new)
        path = reverse('add_patient')
        self.assertFalse(can_access_patient(new, self.patient.pk))
        CareRelationship.objects.filter(patient=self.patient, doctor=self.doctor, status='active').update(is_primary=True)
        old_primary = CareRelationship.objects.get(patient=self.patient, is_primary=True)
        self.assertEqual(self.client.post(path, {'username':self.patient.username, 'doctor_id':self.editor.pk}).status_code, 302)
        self.assertTrue(can_access_patient(new, self.patient.pk, clinical_write=True))
        self.client.post(path, {'username':self.patient.username})
        self.assertEqual(CareRelationship.objects.filter(patient=self.patient, doctor=new, status='active').count(), 1)
        old_primary.refresh_from_db()
        self.assertTrue(old_primary.is_primary)
        self.assertEqual(CareRelationship.objects.filter(patient=self.patient, is_primary=True).count(), 1)
        self.assertContains(self.client.get('/'), self.patient.username)

    def test_attachment_denies_other_roles_invalid_names_and_missing_csrf(self):
        path = reverse('add_patient')
        self.assertEqual(self.client.post(path, {'username':self.patient.username}).status_code, 302)
        for actor in (self.patient, self.editor):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(path, {'username':self.patient.username}).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.doctor)
        self.assertEqual(client.post(path, {'username':self.patient.username}).status_code, 403)
        self.client.force_login(self.doctor)
        for username in ('does-not-exist', self.editor.username, self.doctor.username):
            self.assertContains(self.client.post(path, {'username':username}), 'Активный пациент с таким логином не найден.')

    def test_prototype_self_service_can_be_disabled(self):
        with override_settings(PROTOTYPE_SELF_SERVICE_ENABLED=False):
            self.assertEqual(self.client.get(reverse('register_doctor')).status_code, 403)
            self.assertEqual(self.client.get(reverse('register_patient')).status_code, 403)
            self.client.force_login(self.doctor)
            self.assertEqual(self.client.get(reverse('add_patient')).status_code, 403)
            with self.assertRaises(DomainError):
                onboarding.attach_patient(self.doctor, self.patient.username)
        with override_settings(APP_ENV='production-like'):
            self.client.logout()
            self.assertEqual(self.client.post(reverse('register_doctor'), self.payload()).status_code, 403)
            self.assertEqual(self.client.post(reverse('register_patient'), self.payload()).status_code, 403)

    def test_patient_registration_without_code_does_not_consume_invitation_or_link(self):
        import time
        client = Client(enforce_csrf_checks=True)
        path = reverse('register_patient')
        self.assertEqual(client.post(path, self.payload()).status_code, 403)
        client.get(path)
        token = client.cookies['csrftoken'].value
        invite, _ = invitations.issue(self.doctor, role='patient')
        session = client.session
        session['invitation_context'] = {'digest':invite.token_digest, 'until':time.time()+600}
        session.save()
        result = client.post(path, self.payload(username='prototype_new_patient', role='doctor',
            doctor_id=self.doctor.pk, is_staff='on', csrfmiddlewaretoken=token))
        self.assertEqual(result.status_code, 302)
        patient = get_user_model().objects.get(username='prototype_new_patient')
        self.assertEqual(patient.profile.role, 'patient')
        self.assertFalse(patient.is_staff or patient.is_superuser or patient.profile.moderator_enabled)
        self.assertFalse(CareRelationship.objects.filter(patient=patient).exists())
        invite.refresh_from_db()
        self.assertIsNone(invite.used_at)
        self.assertNotIn('invitation_context', client.session)
        self.assertEqual(client.get('/').status_code, 200)
        self.assertContains(client.get(reverse('contact')), 'Переписка появится после прикрепления врача.')
        self.assertEqual(client.post(reverse('screening_start'), {'csrfmiddlewaretoken': client.cookies['csrftoken'].value}).status_code, 302)

    def test_unattached_patient_can_accept_invitation_later(self):
        patient = get_user_model()(username='prototype_unattached_patient')
        patient.set_unusable_password()
        onboarding.register_patient(patient)
        self.assertFalse(CareRelationship.objects.filter(patient=patient).exists())
        invitation, _ = invitations.issue(self.doctor, role='patient')
        invitations.accept_for_patient(patient, invitation.token_digest)
        self.assertTrue(can_access_patient(self.doctor, patient.pk))
