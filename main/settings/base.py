from pathlib import Path
from datetime import timedelta

from celery.schedules import crontab

import environ

env = environ.Env()

# BASE_DIR is the project root (parent of main/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read .env file
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)

BASE_DOMAIN = env("BASE_DOMAIN", default="localhost")
BASE_PORT = env("BASE_PORT", default="8001")

# Protocol + domain + optional port for building absolute URLs (emails, templates).
# In production set BASE_SCHEME=https and leave BASE_PORT empty or "443".
BASE_SCHEME = env("BASE_SCHEME", default="http")
_port_suffix = f":{BASE_PORT}" if BASE_PORT not in ("", "80", "443") else ""
BASE_URL = f"{BASE_SCHEME}://{BASE_DOMAIN}{_port_suffix}"


def TENANT_URL(slug):
    """Build an absolute URL for a tenant subdomain, e.g. 'demo' → 'http://demo.localhost:8001'."""
    return f"{BASE_SCHEME}://{slug}.{BASE_DOMAIN}{_port_suffix}"

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    f".{BASE_DOMAIN}",
]

# Application definition
INSTALLED_APPS = [
    "daphne",
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "drf_spectacular",
    "channels",
    "django_celery_results",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.microsoft",
    "allauth.socialaccount.providers.openid_connect",
    "whitenoise.runserver_nostatic",
    "anymail",
    # Project apps
    "main",
    "apps.tenants",
    "apps.accounts",
    "apps.billing",
    "apps.tickets",
    "apps.contacts",
    "apps.kanban",
    "apps.comments",
    "apps.notifications",
    "apps.messaging",
    "apps.attachments",
    "apps.analytics",
    "apps.agents",
    "apps.custom_fields",
    "apps.knowledge",
    "apps.notes",
    "apps.inbound_email",
    "apps.crm",
    "apps.newsfeed",
    "apps.voip",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.tenants.middleware.TenantMiddleware",
    "apps.billing.middleware.SubscriptionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "main.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.tenants.context_processors.tenant_context",
            ],
        },
    },
]

ASGI_APPLICATION = "main.asgi.application"

# Database
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

# Custom User Model
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login/"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kuala_Lumpur"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Redis
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379")

# Cache (Redis db3)
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": f"{REDIS_URL}/3",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
        "KEY_PREFIX": "kanzan",
    }
}

# Session via cache with DB fallback (prevents mass logout on Redis restart)
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_DOMAIN = f".{BASE_DOMAIN}" if BASE_DOMAIN != "localhost" else None
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# CSRF
CSRF_COOKIE_DOMAIN = SESSION_COOKIE_DOMAIN
CSRF_TRUSTED_ORIGINS = [
    f"http://*.{BASE_DOMAIN}:8001",
    f"http://{BASE_DOMAIN}:8001",
    f"https://*.{BASE_DOMAIN}",
    f"https://{BASE_DOMAIN}",
]

# CORS
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGIN_REGEXES = [
    rf"^https?://.*\.{BASE_DOMAIN.replace('.', r'\.')}(:\d+)?$",
]

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",
        "api_default": "200/min",
        "api_heavy": "30/min",
        "webhook": "60/min",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# Simple JWT
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SECRET_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# DRF Spectacular
SPECTACULAR_SETTINGS = {
    "TITLE": "Kanzen Suite API",
    "DESCRIPTION": "Multi-tenant CRM & Ticketing SaaS Platform API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Channels (Redis db5)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [f"{REDIS_URL}/5"],
            "prefix": "kanzan:channels",
        },
    },
}

# Celery (Redis db4 as broker)
CELERY_BROKER_URL = f"{REDIS_URL}/4"
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_WORKER_MAX_TASKS_PER_CHILD = 200
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE_FILENAME = "celerybeat-schedule"
CELERY_BEAT_SCHEDULE = {
    "check-sla-breaches": {
        "task": "apps.tickets.tasks.check_sla_breaches",
        "schedule": 120.0,
    },
    "cleanup-old-notifications": {
        "task": "apps.notifications.tasks.cleanup_old_notifications",
        "schedule": 86400.0,
    },
    "check-overdue-tickets": {
        "task": "apps.tickets.tasks.check_overdue_tickets",
        "schedule": 900.0,  # Every 15 minutes
    },
    "calculate-lead-scores": {
        "task": "apps.crm.tasks.calculate_lead_scores",
        "schedule": 86400.0,  # Daily (nightly)
    },
    "calculate-account-health-scores": {
        "task": "apps.crm.tasks.calculate_account_health_scores",
        "schedule": 86400.0,  # Daily (nightly)
    },
    "kb-stale-alert": {
        "task": "knowledge_base.alert_stale_articles",
        "schedule": crontab(hour=8, minute=0),
    },
    "kb-gap-digest": {
        "task": "knowledge_base.send_gap_digest",
        "schedule": crontab(day_of_week="monday", hour=9, minute=0),
    },
    "cleanup-stale-calls": {
        "task": "apps.voip.tasks.cleanup_stale_calls",
        "schedule": 3600.0,  # Every hour
    },
}

# Stripe
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# Inbound Email
INBOUND_EMAIL_WEBHOOK_SECRET = env("INBOUND_EMAIL_WEBHOOK_SECRET", default="")
MAILGUN_API_KEY = env("MAILGUN_API_KEY", default="")  # For inbound webhook signature verification

# Email (Resend via django-anymail)
# Dev uses filebased backend; prod uses Resend (set in prod.py)
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.filebased.EmailBackend"
)
EMAIL_FILE_PATH = BASE_DIR / "tmp" / "emails"
EMAIL_FILE_PATH.mkdir(parents=True, exist_ok=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="support@kanzan.local")

RESEND_API_KEY = env("RESEND_API_KEY", default="")
ANYMAIL = {
    "RESEND_API_KEY": RESEND_API_KEY,
}

# Allauth
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "optional"
SOCIALACCOUNT_AUTO_SIGNUP = True

# File upload limits
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024  # 25MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

# Logging
_LOG_DIR = BASE_DIR / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.FileHandler",
            "filename": _LOG_DIR / "django.log",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# Jazzmin Admin Theme
JAZZMIN_SETTINGS = {
    "site_title": "Kanzen Suite",
    "site_header": "Kanzen Suite",
    "site_brand": "Kanzen Suite",
    "welcome_sign": "Welcome to Kanzen Suite Admin",
    "copyright": "Kanzen Suite",
    "search_model": ["accounts.User", "tenants.Tenant"],
    "topmenu_links": [
        {"name": "Home", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "API Docs", "url": "/api/docs/", "new_window": True},
        {"app": "tenants"},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "icons": {
        "accounts.User": "fas fa-users",
        "accounts.Role": "fas fa-user-shield",
        "accounts.Permission": "fas fa-key",
        "accounts.TenantMembership": "fas fa-link",
        "accounts.Invitation": "fas fa-envelope-open",
        "accounts.Profile": "fas fa-id-card",
        "tenants.Tenant": "fas fa-building",
        "tenants.TenantSettings": "fas fa-cog",
        "tickets.Ticket": "fas fa-ticket-alt",
        "tickets.TicketStatus": "fas fa-traffic-light",
        "tickets.Queue": "fas fa-layer-group",
        "tickets.SLAPolicy": "fas fa-clock",
        "tickets.BusinessHours": "fas fa-business-time",
        "tickets.PublicHoliday": "fas fa-calendar-day",
        "tickets.SLAPause": "fas fa-pause-circle",
        "contacts.Contact": "fas fa-address-book",
        "contacts.Company": "fas fa-briefcase",
        "contacts.ContactGroup": "fas fa-tags",
        "billing.Plan": "fas fa-credit-card",
        "billing.Subscription": "fas fa-file-invoice-dollar",
        "billing.Invoice": "fas fa-receipt",
        "kanban.Board": "fas fa-columns",
        "messaging.Conversation": "fas fa-comments",
        "notifications.Notification": "fas fa-bell",
        "comments.Comment": "fas fa-comment",
        "comments.ActivityLog": "fas fa-history",
        "attachments.Attachment": "fas fa-paperclip",
        "analytics.ReportDefinition": "fas fa-chart-bar",
        "analytics.ExportJob": "fas fa-download",
        "agents.AgentAvailability": "fas fa-headset",
        "custom_fields.CustomFieldDefinition": "fas fa-puzzle-piece",
        "knowledge.Category": "fas fa-folder",
        "knowledge.Article": "fas fa-book",
    },
    "default_icon_parents": "fas fa-folder",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": True,
    "use_google_fonts_cdn": True,
    "changeform_format": "horizontal_tabs",
}

JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": False,
    "footer_small_text": True,
    "body_small_text": False,
    "brand_small_text": False,
    "brand_colour": "navbar-dark",
    "accent": "accent-primary",
    "navbar": "navbar-dark",
    "no_navbar_border": True,
    "navbar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": False,
    "theme": "default",
    "dark_mode_theme": None,
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success",
    },
}
