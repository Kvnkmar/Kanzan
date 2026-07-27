"""
Celery application configuration for CRM.

Uses Redis db4 as broker, django-db as result backend.
All queues are prefixed with 'crm_' to avoid conflicts
with other projects on the same server.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")

app = Celery("crm")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Route tasks to CRM-specific queues
app.conf.task_routes = {
    "apps.billing.tasks.*": {"queue": "crm_webhooks"},
    "apps.notifications.tasks.send_email_*": {"queue": "crm_email"},
    "apps.notifications.tasks.send_notification_email": {"queue": "crm_email"},
    "apps.inbound_email.tasks.*": {"queue": "crm_email"},
    "apps.tickets.tasks.send_ticket_*": {"queue": "crm_email"},
    "apps.api_keys.tasks.send_api_key_*": {"queue": "crm_email"},
    "apps.voip.tasks.*": {"queue": "crm_voip"},
    "*": {"queue": "crm_default"},
}

app.conf.task_default_queue = "crm_default"
