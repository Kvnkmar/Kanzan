from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import (
    AuthViewSet,
    InvitationViewSet,
    ProfileViewSet,
    RoleViewSet,
    TenantMembershipViewSet,
    UserViewSet,
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"profiles", ProfileViewSet, basename="profile")
router.register(r"roles", RoleViewSet, basename="role")
router.register(r"invitations", InvitationViewSet, basename="invitation")
router.register(r"memberships", TenantMembershipViewSet, basename="membership")

# Auth routes are registered manually for cleaner URL structure
auth_urlpatterns = [
    path("register/", AuthViewSet.as_view({"post": "register"}), name="auth-register"),
    path("login/", AuthViewSet.as_view({"post": "login"}), name="auth-login"),
    path("logout/", AuthViewSet.as_view({"post": "logout"}), name="auth-logout"),
    path(
        "accept-invitation/",
        AuthViewSet.as_view({"post": "accept_invitation"}),
        name="auth-accept-invitation",
    ),
]

app_name = "accounts"

urlpatterns = [
    path("", include(router.urls)),
    path("auth/", include((auth_urlpatterns, "auth"))),
]
