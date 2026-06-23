"""
Frontend (template-rendered) URL configuration.

These routes serve Bootstrap-powered HTML pages. All data is loaded
via JavaScript calls to the DRF API endpoints.
"""

from django.urls import path
from django.views.generic import RedirectView

from apps.tenants import frontend_views as views

app_name = "frontend"

urlpatterns = [
    path("", views.landing_page, name="landing"),
    path("login/", views.login_page, name="login"),
    path("register/", views.register_page, name="register"),
    path("logout/", views.logout_page, name="logout"),
    path("auth/handoff/", views.auth_handoff, name="auth-handoff"),
    path("verify-email/", views.verify_email_page, name="verify-email"),
    path("verify-email-sent/", views.verify_email_sent_page, name="verify-email-sent"),
    path("setup-company/", views.setup_company_page, name="setup-company"),
    path("workspaces/", views.workspaces_page, name="workspaces"),
    path("dashboard/", views.dashboard_page, name="dashboard"),
    path("tickets/", views.ticket_list_page, name="ticket-list"),
    path("tickets/new/", views.ticket_create_page, name="ticket-create"),
    path("tickets/<str:ticket_number>/", views.ticket_detail_page, name="ticket-detail"),
    path("contacts/", views.contact_list_page, name="contact-list"),
    path("contacts/create/", views.contact_create_page, name="contact-create"),
    path("contacts/<str:contact_id>/", views.contact_detail_page, name="contact-detail"),
    path("calendar/", views.calendar_page, name="calendar"),
    path("kanban/", views.kanban_page, name="kanban"),
    path("messaging/", views.messaging_page, name="messaging"),
    path("analytics/", views.analytics_page, name="analytics"),
    path("users/", views.users_page, name="users"),
    path("settings/", views.settings_page, name="settings"),
    path("billing/", views.billing_page, name="billing"),
    path("agents/", views.agents_page, name="agents"),
    path("groups/", views.groups_page, name="groups"),
    path("inbox/", views.emails_page, name="inbox"),
    path("knowledge/", views.knowledge_list_page, name="knowledge-list"),
    path("knowledge/<str:article_slug>/", views.knowledge_article_page, name="knowledge-article"),
    path("profile/", views.profile_page, name="profile"),
    path("api/quickstart/", views.api_quickstart_page, name="api-quickstart"),
    path("inbound-email/", views.inbound_email_page, name="inbound-email"),
    path("emails/", views.inbox_hub_page, name="emails"),
    # Legacy URL: the triage desk used to live at /inbox-hub/ before it was
    # relabelled "Emails". Bounce old bookmarks/links to the new path.
    path(
        "inbox-hub/",
        RedirectView.as_view(url="/emails/", query_string=True),
        name="inbox-hub-redirect",
    ),
    path("reminders/", views.reminders_page, name="reminders"),
    path("audit-log/", views.audit_log_page, name="audit-log"),
    path("calls/", views.calls_page, name="calls"),
]
