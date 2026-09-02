from __future__ import annotations

from rest_framework.pagination import CursorPagination


class SafeCursorPagination(CursorPagination):
    page_size = 25
    max_page_size = 100
    page_size_query_param = "limit"
    cursor_query_param = "after"
    ordering = ("-created_at", "-id")
