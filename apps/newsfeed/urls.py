from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.newsfeed.views import NewsPostViewSet

app_name = "newsfeed"

router = DefaultRouter()
router.register(r"posts", NewsPostViewSet, basename="newspost")

urlpatterns = [
    path("", include(router.urls)),
]
