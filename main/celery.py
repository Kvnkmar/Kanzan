"""
Celery application configuration for Kanzen.

Uses Redis db4 as broker, django-db as result backend.
All queues are prefixed with 'kanzan_' to avoid conflicts
with other projects on the same server.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")

app = Celery("kanzan")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Route tasks to Kanzen-specific queues
app.conf.task_routes = {
    "apps.billing.tasks.*": {"queue": "kanzan_webhooks"},
    "apps.notifications.tasks.send_email_*": {"queue": "kanzan_email"},
    "apps.notifications.tasks.send_notification_email": {"queue": "kanzan_email"},
    "apps.inbound_email.tasks.*": {"queue": "kanzan_email"},
    "apps.tickets.tasks.send_ticket_*": {"queue": "kanzan_email"},
    "apps.voip.tasks.*": {"queue": "kanzan_voip"},
    "*": {"queue": "kanzan_default"},
}

app.conf.task_default_queue = "kanzan_default"
