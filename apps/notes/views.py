from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import IsTenantMember
from apps.notes.models import QuickNote
from apps.notes.serializers import QuickNoteSerializer


@extend_schema_view(
    list=extend_schema(summary="List quick notes", tags=["Notes"]),
    create=extend_schema(summary="Create a quick note", tags=["Notes"]),
    retrieve=extend_schema(summary="Retrieve a quick note", tags=["Notes"]),
    update=extend_schema(summary="Replace a quick note", tags=["Notes"]),
    partial_update=extend_schema(summary="Patch a quick note", tags=["Notes"]),
    destroy=extend_schema(summary="Delete a quick note", tags=["Notes"]),
)
class QuickNoteViewSet(viewsets.ModelViewSet):
    """CRUD for personal quick notes. Each user sees only their own notes."""

    serializer_class = QuickNoteSerializer
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return QuickNote.objects.none()
        return QuickNote.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
