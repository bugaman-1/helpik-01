"""HTTPS forms also work for browsers that omit the Origin header."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from quiz.models import Profile
from quiz.services import invitations


@override_settings(ALLOWED_HOSTS=['demo.trycloudflare.com'],
                   CSRF_TRUSTED_ORIGINS=['https://demo.trycloudflare.com'],
                   DEMO_EMBEDDED_FORMS_ENABLED=False)
class HttpsFormTests(TestCase):
    base = 'https://demo.trycloudflare.com'

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST='demo.trycloudflare.com')
        self.doctor = get_user_model().objects.create_user(username='synthetic_https', password='test-password-123')
        Profile.objects.create(user=self.doctor, role='doctor', verification_status='approved')

    def submit_without_origin(self, name, data):
        path = reverse(name)
        page = self.client.get(path, secure=True)
        # Model a browser obeying the page's referrer policy, without Origin.
        headers = {} if page['Referrer-Policy'] == 'no-referrer' else {'HTTP_REFERER': self.base + path}
        data['csrfmiddlewaretoken'] = self.client.cookies['csrftoken'].value
        return self.client.post(path, data, secure=True, **headers)

    def test_login_without_origin(self):
        response = self.submit_without_origin('login', {'username': self.doctor.username, 'password': 'test-password-123'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.doctor.pk)

    def test_invitation_without_origin(self):
        _, token = invitations.issue(self.doctor, role='patient')
        response = self.submit_without_origin('invitation_exchange', {'token': token, 'selected_role': 'patient'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('invitation_context', self.client.session)

    def test_foreign_origin_referer_and_missing_token_remain_rejected(self):
        path = reverse('login')
        page = self.client.get(path, secure=True)
        payload = {'csrfmiddlewaretoken': self.client.cookies['csrftoken'].value}
        for headers in ({'HTTP_ORIGIN': 'https://foreign.invalid'},
                        {'HTTP_REFERER': 'https://foreign.invalid/'},
                        {'HTTP_ORIGIN': 'null'}, {}):
            self.assertEqual(self.client.post(path, payload, secure=True, **headers).status_code, 403)
        self.assertEqual(self.client.post(path, {}, secure=True, HTTP_REFERER=self.base + path).status_code, 403)
