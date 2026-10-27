from ipaddress import ip_address
from urllib.parse import urlsplit
from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware


def is_loopback_demo(request):
    if settings.APP_ENV != "demo" or not settings.DEMO_EMBEDDED_FORMS_ENABLED:
        return False
    try:
        return (
            ip_address(request.META.get("REMOTE_ADDR", "")).is_loopback
            and urlsplit("http://" + request.get_host()).hostname in {"localhost", "127.0.0.1", "::1"}
        )
    except ValueError:
        return False


class LocalDemoCsrfViewMiddleware(CsrfViewMiddleware):
    """Sandboxed embedded forms have opaque Origin. Token checks remain mandatory.

    Explicit local demo compatibility only; no CORS access is enabled. Every
    normal origin and every non-demo request uses Django's unmodified checks.
    """
    def _origin_verified(self, request):
        if request.META.get("HTTP_ORIGIN") == "null" and is_loopback_demo(request):
            return True
        return super()._origin_verified(request)


class PrivateResponsesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = settings.SECURE_REFERRER_POLICY
        return response
