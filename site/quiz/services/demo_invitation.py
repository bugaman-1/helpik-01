"""Read the explicitly issued local demonstration invitation; GET never issues it."""
import json
from pathlib import Path
from django.conf import settings
from quiz.middleware import is_loopback_demo
from .invitations import digest_token, _available
from .contracts import DomainError


def local_ui_demo_tokens(request):
    if not is_loopback_demo(request):
        return {}
    try:
        data = json.loads((settings.BASE_DIR / "runtime/mvp1-test-invitations.json").read_text(encoding="utf-8"))
        result = {}
        for role in ("patient",):
            try:
                token = data[role]["token"]
                invitation = _available(digest_token(token))
            except (KeyError, TypeError, DomainError):
                continue
            if invitation.role == role and invitation.issuer.username.startswith("synthetic_mvp1_"):
                result[role] = token
        return result
    except (OSError, ValueError, KeyError, TypeError, DomainError):
        return {}


def local_demo_token(request):
    if not is_loopback_demo(request):
        return None
    try:
        data = json.loads(Path(settings.DEMO_INVITATION_FILE).read_text(encoding="utf-8"))
        token = data["token"]
        invitation = _available(digest_token(token))
        if invitation.role == "patient" and invitation.issuer.username == "synthetic_invitation_doctor":
            return token
    except (OSError, ValueError, KeyError, TypeError, DomainError):
        pass
    return None
