from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),

    # API v1 endpoints
    path("api/v1/tenants/", include("apps.tenants.urls")),
    path("api/v1/accounts/", include("apps.accounts.urls")),
    path("api/v1/tickets/", include("apps.tickets.urls")),
    path("api/v1/contacts/", include("apps.contacts.urls")),
    path("api/v1/billing/", include("apps.billing.urls")),
    path("api/v1/kanban/", include("apps.kanban.urls")),
    path("api/v1/comments/", include("apps.comments.urls")),
    path("api/v1/messaging/", include("apps.messaging.urls")),
    path("api/v1/notifications/", include("apps.notifications.urls")),
    path("api/v1/attachments/", include("apps.attachments.urls")),
    path("api/v1/analytics/", include("apps.analytics.urls")),
    path("api/v1/agents/", include("apps.agents.urls")),
    path("api/v1/custom-fields/", include("apps.custom_fields.urls")),
    path("api/v1/knowledge/", include("apps.knowledge.urls")),
    path("api/v1/notes/", include("apps.notes.urls")),
    path("api/v1/inbound-email/", include("apps.inbound_email.api_urls")),
    path("api/v1/emails/", include("apps.inbound_email.api_urls", namespace="emails_api")),
    path("api/v1/crm/", include("apps.crm.urls")),
    path("api/v1/nav/", include("apps.nav.urls")),

    # Inbound email webhooks (provider callbacks, not tenant-scoped API)
    path("inbound/email/", include("apps.inbound_email.urls")),

    # API Schema / Docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # Allauth (SSO)
    path("accounts/", include("allauth.urls")),

    # Frontend views (template-based)
    path("", include("apps.tenants.frontend_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
