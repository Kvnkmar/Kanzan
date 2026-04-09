from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.crm.views import ActivityViewSet, PipelineForecastView, ReminderViewSet

app_name = "crm"

router = DefaultRouter()
router.register(r"activities", ActivityViewSet, basename="activity")
router.register(r"reminders", ReminderViewSet, basename="reminder")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "pipeline/<uuid:pipeline_id>/forecast/",
        PipelineForecastView.as_view(),
        name="pipeline-forecast",
    ),
]
