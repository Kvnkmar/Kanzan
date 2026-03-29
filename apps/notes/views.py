from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.accounts.permissions import IsTenantMember
from apps.notes.models import QuickNote
from apps.notes.serializers import QuickNoteSerializer


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
