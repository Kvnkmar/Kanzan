"""
Frontend (template-rendered) URL configuration.

These routes serve Bootstrap-powered HTML pages. All data is loaded
via JavaScript calls to the DRF API endpoints.
"""

from django.urls import path

from apps.tenants import frontend_views as views

app_name = "frontend"

urlpatterns = [
    path("", views.landing_page, name="landing"),
    path("login/", views.login_page, name="login"),
    path("register/", views.register_page, name="register"),
    path("logout/", views.logout_page, name="logout"),
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
    path("knowledge/", views.knowledge_list_page, name="knowledge-list"),
    path("knowledge/<str:article_slug>/", views.knowledge_article_page, name="knowledge-article"),
    path("profile/", views.profile_page, name="profile"),
    path("inbound-email/", views.inbound_email_page, name="inbound-email"),
]
