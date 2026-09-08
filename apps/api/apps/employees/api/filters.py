"""
Filters for the document management page.

`filterset_fields` — a list of field names — covers exact matches and nothing
else, which is enough for a profile tab and not enough for a queue somebody
works through. A reviewer asks "what came in this week, for this department,
that nobody has looked at yet", and that needs ranges.

Every filter here narrows an ALREADY-SCOPED queryset. None of them can widen
what the caller may see: the viewset applies `scope_queryset` first, and a
filter can only remove rows from what that returned. That ordering is what
makes it safe to filter by employee id without it becoming a way to read
another department's records.
"""

from __future__ import annotations

import django_filters as filters
from django.db.models import F

from apps.employees.models import EmployeeDocument, VerificationStatus


class EmployeeDocumentFilter(filters.FilterSet):
    """Everything the Documents page offers, and nothing it does not."""

    status = filters.ChoiceFilter(choices=VerificationStatus.choices)
    employee = filters.UUIDFilter(field_name="employee_id")
    department = filters.UUIDFilter(field_name="employee__department_id")
    document_type = filters.UUIDFilter(field_name="document_type_id")
    uploaded_by = filters.UUIDFilter(field_name="uploaded_by_id")
    #: The same question keyed by EMPLOYEE rather than by login. The UI has a
    #: roster to build a picker from and no list of user accounts, and "who
    #: filed this" is a question people ask about a colleague, not about a row
    #: in the auth table.
    uploaded_by_employee = filters.UUIDFilter(field_name="uploaded_by__employee__id")

    uploaded_after = filters.DateFilter(field_name="uploaded_at", lookup_expr="date__gte")
    uploaded_before = filters.DateFilter(field_name="uploaded_at", lookup_expr="date__lte")

    #: "Show me the ones somebody filed on another person's behalf." Kept as a
    #: filter rather than left to the reader's eye, because it is the case a
    #: reviewer most needs to be able to isolate.
    filed_by_someone_else = filters.BooleanFilter(method="_filed_by_someone_else")

    class Meta:
        model = EmployeeDocument
        fields = [
            "status",
            "employee",
            "department",
            "document_type",
            "uploaded_by",
            "uploaded_by_employee",
            "uploaded_after",
            "uploaded_before",
        ]

    def _filed_by_someone_else(self, queryset, name, value):
        """
        Rows where the uploader is not the employee's own login.

        An upload with no uploader recorded (a data migration, a fixture)
        counts as neither: it has no author to compare, and guessing would put
        rows in front of a reviewer under a claim the data does not support.
        """
        if value is None:
            return queryset
        mismatched = queryset.exclude(uploaded_by__isnull=True).exclude(
            uploaded_by_id=F("employee__user_id")
        )
        if value:
            return mismatched
        return queryset.exclude(pk__in=mismatched.values("pk"))
