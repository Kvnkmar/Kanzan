"""
URL configuration for the kanban app.

Provides nested routing under boards:
    /boards/                              -- BoardViewSet (list, create)
    /boards/{board_pk}/                   -- BoardViewSet (retrieve, update, delete)
    /boards/{board_pk}/detail/            -- BoardViewSet.detail_with_cards
    /boards/{board_pk}/columns/           -- ColumnViewSet (list, create)
    /boards/{board_pk}/columns/{pk}/      -- ColumnViewSet (retrieve, update, delete)
    /boards/{board_pk}/cards/             -- CardPositionViewSet (list, create)
    /boards/{board_pk}/cards/{pk}/        -- CardPositionViewSet (retrieve, update, delete)
    /boards/{board_pk}/cards/move/        -- CardPositionViewSet.move
    /boards/{board_pk}/cards/reorder/     -- CardPositionViewSet.reorder

Include in the project root URL conf::

    path("api/v1/kanban/", include("apps.kanban.urls")),
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.kanban.views import BoardViewSet, CardPositionViewSet, ColumnViewSet

app_name = "kanban"

# Top-level router for boards.
router = DefaultRouter()
router.register(r"boards", BoardViewSet, basename="board")

# Nested routes under a specific board.
board_nested_patterns = [
    path(
        "columns/",
        ColumnViewSet.as_view({"get": "list", "post": "create"}),
        name="board-columns-list",
    ),
    path(
        "columns/<uuid:pk>/",
        ColumnViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="board-columns-detail",
    ),
    path(
        "cards/",
        CardPositionViewSet.as_view({"get": "list", "post": "create"}),
        name="board-cards-list",
    ),
    path(
        "cards/move/",
        CardPositionViewSet.as_view({"post": "move"}),
        name="board-cards-move",
    ),
    path(
        "cards/reorder/",
        CardPositionViewSet.as_view({"post": "reorder"}),
        name="board-cards-reorder",
    ),
    path(
        "cards/<uuid:pk>/",
        CardPositionViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="board-cards-detail",
    ),
]

urlpatterns = [
    path("", include(router.urls)),
    path(
        "boards/<uuid:board_pk>/",
        include((board_nested_patterns, "board-nested")),
    ),
]
