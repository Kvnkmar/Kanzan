from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notes.views import QuickNoteViewSet

app_name = "notes"

router = DefaultRouter()
router.register(r"notes", QuickNoteViewSet, basename="quicknote")

urlpatterns = [
    path("", include(router.urls)),
]
