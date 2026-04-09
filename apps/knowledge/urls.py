"""URL configuration for the Knowledge Base app."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.knowledge.views import ArticleViewSet, CategoryViewSet, KBSearchView

app_name = "knowledge"

router = DefaultRouter()
router.register(r"categories", CategoryViewSet, basename="kb-category")
router.register(r"articles", ArticleViewSet, basename="kb-article")

urlpatterns = [
    path("search/", KBSearchView.as_view(), name="kb-search"),
    path("", include(router.urls)),
]
