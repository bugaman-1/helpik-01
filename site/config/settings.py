"""B01 settings. No secrets or original database selected implicitly."""
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
APP_ENV = os.environ.get("APP_ENV", "local")
if APP_ENV not in {"local", "demo", "production-like", "test"}:
    raise ImproperlyConfigured("Unknown APP_ENV")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a random secret of at least 50 characters")
DEBUG = False
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]
if not ALLOWED_HOSTS or any(h.startswith(".") or "*" in h or "/" in h for h in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Exact DJANGO_ALLOWED_HOSTS required")
CSRF_TRUSTED_ORIGINS = [h.strip() for h in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if h.strip()]
if any("*" in h or not h.startswith("https://") for h in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Exact HTTPS CSRF origins required")
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "quiz"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "quiz.middleware.LocalDemoCsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "quiz.middleware.PrivateResponsesMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "quiz.context_processors.prototype_features"]}}]
WSGI_APPLICATION = "config.wsgi.application"
db_path = Path(os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "runtime" / (APP_ENV + ".sqlite3")))).resolve()
if db_path == (BASE_DIR / "db.sqlite3").resolve():
    raise ImproperlyConfigured("Original database is protected. Use a verified backup/copy.")
db_path.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": db_path, "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"}, "TEST": {"NAME": str(BASE_DIR / "runtime" / "tests.sqlite3")}}}
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation." + name} for name in ["UserAttributeSimilarityValidator", "MinimumLengthValidator", "CommonPasswordValidator", "NumericPasswordValidator"]]
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
LOGIN_URL = "login"
CSRF_FAILURE_VIEW = "quiz.access_views.csrf_failure"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# HTTPS form checks need Referer when the browser omits Origin.
# Keep the page address private on links to other sites.
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = APP_ENV == "production-like"
SECURE_SSL_REDIRECT = APP_ENV == "production-like"
SECURE_HSTS_SECONDS = 3600 if APP_ENV == "production-like" else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = APP_ENV == "production-like"
SECURE_HSTS_PRELOAD = False
# No proxy headers are trusted by default; B12 owns proxy/TLS deployment.
CLINICAL_PUBLICATION_ENABLED = False
WEBPUSH_PRIVATE_KEY = BASE_DIR / "runtime" / "webpush-private.pem"
WEBPUSH_SUBJECT = os.environ.get("WEBPUSH_SUBJECT", "https://localhost")
DEMO_SURVEY_ENABLED = APP_ENV in {"demo", "test"}
PROTOTYPE_SELF_SERVICE_ENABLED = APP_ENV in {"demo", "test"}
DEMO_EMBEDDED_FORMS_ENABLED = APP_ENV == "demo" and os.environ.get("DEMO_EMBEDDED_FORMS_ENABLED") == "1"
DEMO_INVITATION_FILE = BASE_DIR / "runtime" / "demo-registration-invitation.json"
DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
# Do not log request paths, bodies, cookies, invitation tokens or debug tracebacks.
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"null": {"class": "logging.NullHandler"}}, "loggers": {name: {"handlers": ["null"], "propagate": False} for name in ["django.request", "django.server", "django.security"]}}
