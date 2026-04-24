DEBUG = True

# In dev, allow all localhost variants
ALLOWED_HOSTS = ["*"]

# Email backend is configured via .env / base.py
# Set EMAIL_BACKEND in .env to override (filebased saves to tmp/emails/)

# Disable whitenoise compression in dev
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8001",
    "http://*.localhost:8001",
]
