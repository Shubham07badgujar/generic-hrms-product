"""
Employee API.

Reads are scoped automatically by `ScopedModelViewSet` — a Department Head
hitting `/employees/` sees their department, a Layer-5 employee sees only
themselves, and neither view writes a filter to make that happen.

Creation delegates wholly to `create_employee`, so the atomic transaction and
every hierarchy rule apply identically over HTTP, from a command, or from a
future bulk import.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from apps.employees.models import Employee
from apps.employees.services.creation import create_employee
from apps.employees.services.lifecycle import remove_employee
from core.access import Action, Resource
from core.access.drf import ScopedModelViewSet

from .serializers import (
    EmployeeCreateSerializer,
    EmployeeDetailSerializer,
    EmployeeListSerializer,
    IdentifiersSerializer,
)


def _layer_of(employee) -> int | None:
    """The layer of an employee's most senior live role, for display."""
    if employee.user_id is None:
        return None
    layers = [
        grant.role.layer
        for grant in employee.user.user_roles.all()
        if grant.is_active and grant.role.is_active
    ]
    return min(layers) if layers else None


class EmployeeViewSet(ScopedModelViewSet):
    access_resource = Resource.EMPLOYEE

    queryset = (
        Employee.objects.select_related(
            "department", "designation", "location", "level", "team", "reporting_manager", "user"
        )
        .prefetch_related("user__user_roles__role")
        .filter(is_active=True)
    )
    serializer_class = EmployeeListSerializer
    filterset_fields = [
        "department", "status", "employment_type", "team",
        "designation", "location", "reporting_manager", "probation_status",
    ]
    search_fields = ["employee_code", "first_name", "last_name", "work_email"]
    ordering_fields = ["employee_code", "date_of_joining", "first_name", "status"]

    #: Custom actions must declare their real authority. Without this,
    #: `change_status` would resolve through the HTTP-method fallback to
    #: CREATE, demanding the wrong permission entirely.
    access_actions = {
        "me": Action.VIEW,
        "reports": Action.VIEW,
        "profile": Action.VIEW,
        "change_status": Action.EDIT,
        "identifiers": Action.EDIT,
        "reporting_manager": Action.EDIT,
        "resend_credentials": Action.EDIT,
        "change_name": Action.EDIT,
        "eligible_managers": Action.VIEW,
    }

    def destroy(self, request, *args, **kwargs):
        """
        DELETE /employees/<id>/ — remove a FORMER employee from the system.

        Through the service, not the generic destroy: the generic path would
        soft-delete the row and leave the person's login and roles live. The
        service refuses anyone not exited/terminated, switches the login off,
        and audits it with the optional reason in the body.
        """
        employee = self.get_object()
        reason = ""
        if isinstance(request.data, dict):
            reason = str(request.data.get("reason", "") or "")[:500]
        try:
            remove_employee(employee=employee, actor=request.user, reason=reason)
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            raise DRFValidationError(detail) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    def get_serializer_class(self):
        if self.action == "create":
            return EmployeeCreateSerializer
        if self.action in ("retrieve", "me"):
            return EmployeeDetailSerializer
        return EmployeeListSerializer

    def create(self, request, *args, **kwargs):
        """
        POST /api/v1/employees/ — create person, placement, role and login atomically.

        `create_employee` raises AccessDenied (403) or ValidationError (400);
        both are shaped by the global exception handler, so the field-attributed
        hierarchy messages reach the client intact.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payload = {k: v for k, v in serializer.validated_data.items() if v not in (None, "")}
        result = create_employee(actor=request.user, **payload)

        return Response(
            {
                "employee": EmployeeDetailSerializer(result.employee).data,
                "user": {"id": str(result.user.pk), "email": result.user.email},
                "role": result.role.code,
                "password_set": result.temporary_password is not None,
                # The truth, not an assumption. False means the account exists
                # but the credentials did not reach them — HR must act.
                "welcome_email_sent": result.welcome_email_sent,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        """
        GET /api/v1/employees/me/ — the caller's own record.

        Bypasses scoping deliberately: a Layer-5 employee's SELF scope already
        resolves to exactly this row, but CEO and Admin have no Employee at all
        and must receive a clear 404 rather than an empty list.
        """
        employee = getattr(request.user, "employee", None)
        if employee is None:
            return Response(
                {
                    "error": {
                        "code": "no_employee_record",
                        "message": (
                            "This account is not linked to an employee record. "
                            "System-level roles such as CEO and Admin have none."
                        ),
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(EmployeeDetailSerializer(employee).data)

    @action(detail=False, methods=["get"], url_path="eligible-managers")
    def eligible_managers(self, request):
        """
        GET /employees/eligible-managers/?role_code=&employee=

        Who may be a reporting manager for a person in `role_code`. The form
        reads this instead of filtering a list itself, so what the dropdown
        offers and what the server accepts come from one function and cannot
        drift apart. Pass `employee` when editing an existing person — their
        own reporting tree is then excluded, since those are exactly the
        choices that would create a loop.
        """
        from apps.accounts.models import Role

        from ..services.hierarchy import eligible_reporting_managers

        role_code = request.query_params.get("role_code", "")
        role = Role.objects.filter(code=role_code, is_active=True).first()
        if role is None:
            raise DRFValidationError({"role_code": "A valid role_code is required."})

        employee = None
        employee_id = request.query_params.get("employee")
        if employee_id:
            try:
                uuid.UUID(str(employee_id))
            except (ValueError, TypeError):
                raise DRFValidationError({"employee": "Not a valid employee id."})
            employee = Employee.objects.filter(pk=employee_id).first()

        rows = eligible_reporting_managers(role=role, employee=employee)
        return Response(
            [
                {
                    "id": str(row.pk),
                    "employee_code": row.employee_code,
                    "full_name": row.full_name,
                    "department_name": row.department.name if row.department_id else "",
                    "layer": _layer_of(row),
                }
                for row in rows
            ]
        )

    @action(detail=True, methods=["patch"], url_path="reporting-manager")
    def reporting_manager(self, request, pk=None):
        """
        PATCH /employees/{id}/reporting-manager/ — repoint one reporting line.

        The employee record is read-only over the general endpoint; changing
        the org chart is a narrow, audited write. Restricted to organisation-
        wide EMPLOYEE/EDIT (Admin, HR Head, HR Manager): a reporting line
        decides who approves that person's leave, so it is not something a
        team lead may quietly redirect.

        Send `{"reporting_manager": "<employee id>"}`, or null to clear it.
        """
        from core.access.catalog import Scope
        from core.access.engine import can

        if can(request.user, Resource.EMPLOYEE, Action.EDIT) < Scope.ALL:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Reporting lines are maintained by HR (organisation-wide "
                "employee-edit authority)."
            )

        employee = self.get_object()
        raw = request.data.get("reporting_manager", "")
        manager = None
        if raw not in (None, ""):
            try:
                uuid.UUID(str(raw))
            except (ValueError, TypeError):
                raise DRFValidationError({"reporting_manager": "Not a valid employee id."})
            manager = Employee.objects.filter(pk=raw).first()
            if manager is None:
                raise DRFValidationError({"reporting_manager": "No such employee."})

        from ..services.hierarchy import set_reporting_manager

        try:
            set_reporting_manager(
                employee=employee,
                manager=manager,
                actor=request.user,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except DjangoValidationError as exc:
            detail = (
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            )
            raise DRFValidationError(detail) from exc

        employee.refresh_from_db()
        return Response(EmployeeDetailSerializer(employee, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="resend-credentials")
    def resend_credentials(self, request, pk=None):
        """
        POST /employees/{id}/resend-credentials/ — issue a fresh temporary
        password and send the welcome mail again.

        For the case a welcome mail is lost AFTER we did everything right: a
        provider can accept the message at SMTP and bounce it afterwards to the
        sender's mailbox, so the original send looks successful here while the
        employee never received it. Without this the only remedy was deleting
        and recreating the person.

        Restricted to organisation-wide EMPLOYEE/EDIT, like every other
        credential-adjacent write.
        """
        from core.access.catalog import Scope
        from core.access.engine import can

        if can(request.user, Resource.EMPLOYEE, Action.EDIT) < Scope.ALL:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Credentials are reissued by HR (organisation-wide employee-edit authority)."
            )

        employee = self.get_object()
        from apps.accounts.services.passwords import reissue_credentials

        try:
            sent = reissue_credentials(
                employee=employee,
                actor=request.user,
                reason=str(request.data.get("reason", "")).strip(),
            )
        except DjangoValidationError as exc:
            detail = (
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            )
            raise DRFValidationError(detail) from exc

        recipient = (employee.personal_email or "").strip() or (
            employee.user.email if employee.user_id else ""
        )
        return Response(
            {
                "sent": sent,
                "recipient": recipient,
                "detail": (
                    f"A new temporary password was emailed to {recipient}. Their previous "
                    f"password no longer works."
                    if sent
                    else (
                        f"A new temporary password was set, but the email to {recipient} "
                        f"could not be sent. Check the mail configuration and try again."
                    )
                ),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["patch"], url_path="name")
    def change_name(self, request, pk=None):
        """
        PATCH /employees/{id}/name/ — correct or update the name on a record.

        Two callers, one rule set: HR fixing a misspelling on anyone, and the
        employee themselves after a marriage or legal change. Nothing in the
        permission matrix changes for this — every account already holds
        EMPLOYEE/EDIT, self-scoped for staff and organisation-wide for HR, and
        that existing grant decides who may reach whom.

        The scope is re-checked against EDIT explicitly. A custom action scopes
        its row lookup by VIEW, and for several roles VIEW is WIDER than EDIT —
        a Medical Director sees their whole department but may only edit their
        own record. Without this check that gap would let them rename a
        colleague.
        """
        from core.access import scope_queryset

        employee = self.get_object()
        editable = scope_queryset(
            Employee.objects.all(), request.user,
            resource=Resource.EMPLOYEE, action=Action.EDIT,
        )
        if not editable.filter(pk=employee.pk).exists():
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You may only change your own name.")

        data = request.data
        if "first_name" not in data and "last_name" not in data and "middle_name" not in data:
            raise DRFValidationError(
                {"first_name": "Send at least one of first_name, middle_name or last_name."}
            )

        from ..services.profile import rename_employee

        try:
            rename_employee(
                employee=employee,
                # Omitted parts keep their current value; sending "" clears an
                # optional one, which is how a person drops a middle name.
                first_name=data.get("first_name", employee.first_name),
                middle_name=data.get("middle_name", employee.middle_name),
                last_name=data.get("last_name", employee.last_name),
                actor=request.user,
                reason=str(data.get("reason", "")).strip(),
            )
        except DjangoValidationError as exc:
            detail = (
                exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            )
            raise DRFValidationError(detail) from exc

        employee.refresh_from_db()
        return Response(EmployeeDetailSerializer(employee, context={"request": request}).data)

    @action(detail=True, methods=["patch"], url_path="identifiers")
    def identifiers(self, request, pk=None):
        """
        PATCH /employees/{id}/identifiers/ — set PAN / Aadhaar / UAN / ESIC /
        bank details. The ONE write path for these fields; reads stay masked.

        Restricted to organisation-wide EMPLOYEE/EDIT (Admin, HR Head, HR
        Manager). Deliberately NOT self-service and NOT team scope: a changed
        bank account is where salary lands, which makes this the classic
        payroll-fraud write — it stays with HR, and every change is audited
        with masked before/after values.
        """
        from core.access.catalog import Scope
        from core.access.engine import can

        if can(request.user, Resource.EMPLOYEE, Action.EDIT) < Scope.ALL:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Statutory and bank identifiers are maintained by HR "
                "(organisation-wide employee-edit authority)."
            )

        employee = self.get_object()
        data = dict(request.data)
        for key in ("pan", "bank_ifsc"):
            if isinstance(data.get(key), str):
                data[key] = data[key].strip().upper()
        payload = IdentifiersSerializer(data=data)
        payload.is_valid(raise_exception=True)

        masked_before = {
            "pan": employee.pan_masked, "aadhaar": employee.aadhaar_masked,
            "bank_account_number": employee.bank_account_masked,
            "bank_ifsc": employee.bank_ifsc, "bank_name": employee.bank_name,
            "uan": employee.uan, "esic_number": employee.esic_number,
        }
        changed = list(payload.validated_data)
        for field, value in payload.validated_data.items():
            setattr(employee, field, value.strip() if isinstance(value, str) else value)
        employee.updated_by = request.user
        employee.save(update_fields=[*changed, "updated_by", "updated_at"])

        from apps.audit.events import record_event

        record_event(
            employee,
            actor=request.user,
            entity_type="employees.Employee",
            verb="update",
            resource=Resource.EMPLOYEE,
            # Masked on both sides: the audit log must show THAT the account
            # changed, never the account number itself.
            before={k: v for k, v in masked_before.items() if k in changed},
            after={
                "pan": employee.pan_masked, "aadhaar": employee.aadhaar_masked,
                "bank_account_number": employee.bank_account_masked,
                "bank_ifsc": employee.bank_ifsc, "bank_name": employee.bank_name,
                "uan": employee.uan, "esic_number": employee.esic_number,
            },
            reason=f"Identifiers updated: {', '.join(sorted(changed))}",
        )
        return Response(EmployeeDetailSerializer(employee, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="reports")
    def reports(self, request, pk=None):
        """GET /api/v1/employees/{id}/reports/ — direct and indirect reports."""
        employee = self.get_object()
        ids = employee.reporting_tree_ids(include_self=False)
        reports = self.queryset.filter(pk__in=ids)
        return Response(EmployeeListSerializer(reports, many=True).data)

    @action(detail=True, methods=["get"], url_path="profile")
    def profile(self, request, pk=None):
        """
        The whole profile, assembled section by section.

        EACH SECTION IS INCLUDED ONLY IF THE CALLER HOLDS ITS OWN PERMISSION.
        Seeing an employee record does not entitle you to their documents,
        their probation assessments or their company account — those are
        separate resources with separate grants. A section the caller cannot
        read is ABSENT from the payload rather than present-and-empty: absent
        is honest, while empty invites the reader to conclude there is nothing
        there.
        """
        from apps.assets.models import AssetAllocation
        from apps.employees.models import EmployeeDocument, ProbationReview
        from apps.employees.services.lifecycle import allowed_targets
        from apps.itaccounts.models import CompanyEmailAccount
        from apps.onboarding.models import EmployeeLetter, EmployeeOnboarding
        from core.access import can
        from core.access.catalog import Scope
        from core.access.engine import scope_queryset

        from .lifecycle_serializers import (
            AssetAllocationSerializer,
            CompanyEmailAccountSerializer,
            EmployeeDocumentSerializer,
            EmployeeLetterSerializer,
            EmployeeOnboardingSerializer,
            ProbationReviewSerializer,
        )

        employee = self.get_object()
        user = request.user

        def section(model, resource, *, related=()):
            """
            One section, scoped by ITS OWN resource.

            Two gates, and both are needed. `can()` decides whether the section
            appears at all; `scope_queryset` decides what it contains. Without
            the second, a viewer holding LETTER at SELF scope would open a
            colleague's profile and read that colleague's letters, because
            reaching the profile at all would have been treated as reaching
            everything on it.
            """
            if not can(user, resource):
                return None
            queryset = model.objects.filter(employee=employee)
            if related:
                queryset = queryset.select_related(*related)
            return scope_queryset(queryset, user, resource=resource, action=Action.VIEW)

        sections: dict = {
            "employee": EmployeeDetailSerializer(employee).data,
            # What the UI may offer as a next status. Gated on scope > SELF to
            # match the service: self-service grants EMPLOYEE/EDIT so people can
            # maintain their own details, and that must not read as authority to
            # move themselves through the lifecycle.
            "allowed_status_transitions": (
                allowed_targets(employee.status)
                if can(user, Resource.EMPLOYEE, Action.EDIT) > Scope.SELF
                else []
            ),
        }

        documents = section(
            EmployeeDocument, Resource.EMPLOYEE_DOCUMENT,
            related=("document_type", "uploaded_by", "verified_by"),
        )
        if documents is not None:
            sections["documents"] = EmployeeDocumentSerializer(
                documents.filter(is_active=True), many=True
            ).data

        onboarding = section(EmployeeOnboarding, Resource.ONBOARDING)
        if onboarding is not None:
            record = onboarding.first()
            sections["onboarding"] = (
                EmployeeOnboardingSerializer(record).data if record else None
            )

        reviews = section(
            ProbationReview, Resource.PROBATION_REVIEW, related=("reviewer", "decided_by")
        )
        if reviews is not None:
            sections["probation_reviews"] = ProbationReviewSerializer(reviews, many=True).data

        allocations = section(
            AssetAllocation, Resource.ASSET_ALLOCATION,
            related=("asset", "asset__category", "allocated_by", "received_by"),
        )
        if allocations is not None:
            sections["asset_allocations"] = AssetAllocationSerializer(
                allocations, many=True
            ).data

        accounts = section(CompanyEmailAccount, Resource.EMAIL_ACCOUNT)
        if accounts is not None:
            record = accounts.first()
            sections["company_account"] = (
                CompanyEmailAccountSerializer(record).data if record else None
            )

        letters = section(EmployeeLetter, Resource.LETTER, related=("generated_by",))
        if letters is not None:
            sections["letters"] = EmployeeLetterSerializer(
                letters.filter(is_active=True), many=True
            ).data

        if can(user, Resource.CANDIDATE) and employee.created_from_candidate_id:
            candidate = employee.created_from_candidate
            sections["recruitment_source"] = {
                "candidate_id": str(candidate.pk),
                "candidate_name": candidate.full_name,
                "source": candidate.source,
                "applied_on": candidate.created_at,
            }

        return Response(sections)

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        """
        Move the employee through the lifecycle.

        Deliberately not a PATCH on `status`: the transition table, the
        mandatory reason for an ending, and the unreturned-asset gate all live
        in the service, and a writable field would bypass every one of them.
        """
        from apps.employees.services.lifecycle import change_status

        from .lifecycle_serializers import StatusChangeSerializer

        payload = StatusChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            employee = change_status(
                employee=self.get_object(),
                actor=request.user,
                new_status=payload.validated_data["status"],
                reason=payload.validated_data.get("reason", ""),
                effective_date=payload.validated_data.get("effective_date"),
            )
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else {"detail": exc.messages}
            raise DRFValidationError(detail) from exc

        return Response(EmployeeDetailSerializer(employee).data)
