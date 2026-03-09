"""
URL configuration for the messaging app.

Provides nested routes: ``conversations/{id}/messages/`` via the DRF router.

Include in the project root URL conf::

    path("api/v1/messaging/", include("apps.messaging.urls")),
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.messaging.views import ConversationViewSet, MessageViewSet

router = DefaultRouter()
router.register(r"conversations", ConversationViewSet, basename="conversation")

# Nested messages route under a specific conversation
message_list = MessageViewSet.as_view({"get": "list", "post": "create"})
message_detail = MessageViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)

app_name = "messaging"

urlpatterns = [
    # Conversation CRUD + extra actions (messages, add-participant, remove-participant)
    path("", include(router.urls)),
    # Nested message routes: conversations/{conversation_pk}/messages/
    path(
        "conversations/<uuid:conversation_pk>/messages/",
        message_list,
        name="conversation-messages-list",
    ),
    path(
        "conversations/<uuid:conversation_pk>/messages/<uuid:pk>/",
        message_detail,
        name="conversation-messages-detail",
    ),
]
