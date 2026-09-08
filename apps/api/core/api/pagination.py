"""
Page-number pagination, 40 records to a page.

This replaces cursor pagination, and the trade is made knowingly. Cursor
pagination is stable under concurrent inserts and O(1) at any depth — the
right defaults for an unbounded feed. But this system's lists are registers
people REVIEW: employees, audit rows, documents. A reviewer needs "page 3 of
7", needs to come back to page 3 tomorrow, and needs to cite it to a
colleague; an opaque cursor can do none of that. At this deployment's scale
(hundreds of rows, not millions) OFFSET cost is unmeasurable, and a row
shifting one place mid-review is a smaller harm than a register nobody can
cite a page of.

The envelope keeps the exact `{data, meta}` shape the SPA already consumes —
`next`/`previous` remain absolute URLs — and adds `count`, `page` and `pages`
so a numbered pager can exist. Old clients that only read `next`/`previous`
keep working untouched.
"""

from __future__ import annotations

from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination as DRFPageNumberPagination
from rest_framework.response import Response


class PageNumberPagination(DRFPageNumberPagination):
    page_size = 40
    page_size_query_param = "page_size"
    max_page_size = 200

    def paginate_queryset(self, queryset, request, view=None):
        # Offset pagination over an unordered queryset hands out rows in
        # whatever order the planner felt like, which makes pages overlap and
        # skip. Most viewsets order explicitly or via Meta.ordering; for any
        # that do not, fall back to the indexed `-created_at` every BaseModel
        # carries, rather than letting the gap surface as duplicated rows.
        if hasattr(queryset, "ordered") and not queryset.ordered:
            queryset = queryset.order_by("-created_at")
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("data", data),
                    (
                        "meta",
                        {
                            "next": self.get_next_link(),
                            "previous": self.get_previous_link(),
                            "page_size": self.get_page_size(self.request),
                            "count": self.page.paginator.count,
                            "page": self.page.number,
                            "pages": self.page.paginator.num_pages,
                        },
                    ),
                ]
            )
        )
