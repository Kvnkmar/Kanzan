from django.urls import path

from apps.nav.views import BadgeCountView

app_name = "nav"

urlpatterns = [
    path("badge-counts/", BadgeCountView.as_view(), name="badge-counts"),
]
