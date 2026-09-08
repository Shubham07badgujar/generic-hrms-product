"""
Employee serializers.

Validation lives in the SERVICE layer, not here. These serializers shape and
type-check the payload; `create_employee` owns every hierarchy rule, so a
management command, a bulk import and this endpoint all get identical
enforcement. A rule implemented in a serializer is a rule the next caller
bypasses.
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from apps.employees.models import Employee


class EmployeeListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    designation_title = serializers.CharField(
        source="designation.title", read_only=True, default=""
    )
    reporting_manager_name = serializers.CharField(
        source="reporting_manager.full_name", read_only=True, default=""
    )
    roles = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            "id",
            "employee_code",
            "full_name",
            "work_email",
            "department",
            "department_name",
            "designation",
            "designation_title",
            "reporting_manager",
            "reporting_manager_name",
            "employment_type",
            "date_of_joining",
            "status",
            "roles",
        ]
        read_only_fields = fields

    def get_roles(self, employee) -> list[str]:
        if employee.user_id is None:
            return []
        return list(
            employee.user.user_roles.filter(is_active=True, role__is_active=True)
            .values_list("role__code", flat=True)
        )


class EmployeeDetailSerializer(EmployeeListSerializer):
    """
    Full profile. PII is exposed MASKED only.

    Unmasked PAN/Aadhaar/bank details are released through a separate,
    audited endpoint where there is a lawful need — never as a side effect of
    opening a profile page.
    """

    pan = serializers.CharField(source="pan_masked", read_only=True)
    aadhaar = serializers.CharField(source="aadhaar_masked", read_only=True)
    bank_account_number = serializers.CharField(
        source="bank_account_masked", read_only=True
    )
    #: Pay is need-to-know: the agreed-at-hire CTC is released only to
    #: holders of SALARY/VIEW beyond self-scope (HR Head, Finance, Admin) and
    #: to the person themselves. Everyone else sees null, not a masked hint.
    annual_ctc = serializers.SerializerMethodField()

    def get_annual_ctc(self, employee) -> str | None:
        if employee.annual_ctc is None:
            return None
        request = self.context.get("request")
        if request is None or not getattr(request.user, "is_authenticated", False):
            return None
        from core.access import Resource, Scope
        from core.access.engine import can

        if can(request.user, Resource.SALARY) > Scope.SELF:
            return str(employee.annual_ctc)
        if employee.user_id == request.user.pk:
            return str(employee.annual_ctc)
        return None

    class Meta(EmployeeListSerializer.Meta):
        fields = EmployeeListSerializer.Meta.fields + [
            "annual_ctc",
            "middle_name",
            "first_name",
            "last_name",
            "personal_email",
            "phone",
            "date_of_birth",
            "gender",
            "location",
            "level",
            "team",
            "probation_start_date",
            "probation_end_date",
            # Distinct from `status`: an employee can be ACTIVE while their
            # probation is still DUE, so the profile has to carry both.
            "probation_status",
            "confirmation_date",
            "date_of_exit",
            "notice_period_days",
            "pan",
            "aadhaar",
            "bank_account_number",
            "bank_ifsc",
            "bank_name",
            "uan",
            "esic_number",
        ]
        read_only_fields = fields


class IdentifiersSerializer(serializers.Serializer):
    """
    The statutory and banking identifiers, WRITE side.

    Reads stay masked on the profile; this is the one sanctioned way to set or
    correct the underlying values. Every field is optional — only what is sent
    changes — and an empty string clears a value. Formats are validated here
    so a typo'd IFSC fails loudly instead of bouncing a salary transfer.
    """

    pan = serializers.RegexField(
        r"^[A-Z]{5}[0-9]{4}[A-Z]$", required=False, allow_blank=True,
        error_messages={"invalid": "PAN must look like AAAAA9999A."},
    )
    aadhaar = serializers.RegexField(
        r"^[0-9]{12}$", required=False, allow_blank=True,
        error_messages={"invalid": "Aadhaar is exactly 12 digits."},
    )
    uan = serializers.RegexField(
        r"^[0-9]{12}$", required=False, allow_blank=True,
        error_messages={"invalid": "UAN is exactly 12 digits."},
    )
    esic_number = serializers.RegexField(
        r"^[0-9]{10,17}$", required=False, allow_blank=True,
        error_messages={"invalid": "ESIC number is 10 to 17 digits."},
    )
    bank_account_number = serializers.RegexField(
        r"^[0-9]{9,18}$", required=False, allow_blank=True,
        error_messages={"invalid": "Bank account number is 9 to 18 digits."},
    )
    bank_ifsc = serializers.RegexField(
        r"^[A-Z]{4}0[A-Z0-9]{6}$", required=False, allow_blank=True,
        error_messages={"invalid": "IFSC must look like HDFC0001234."},
    )
    bank_name = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to update.")
        return attrs


class EmployeeCreateSerializer(serializers.Serializer):
    """
    The atomic-creation payload: person, placement, role and login together.

    Deliberately NOT a ModelSerializer — the request spans three models
    (Employee, User, UserRole) and a ModelSerializer would imply it maps to one.
    """

    # identity
    first_name = serializers.CharField(max_length=100)
    middle_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    #: The COMPANY email — the canonical HRMS account identifier.
    email = serializers.EmailField(
        help_text="Company Email / HRMS account email. Signs the employee in "
                  "(their Personal Email signs in too)."
    )
    #: The PERSONAL email — the ONLY place the setup credentials are sent,
    #: and an accepted login identifier. Required: HR must record both.
    personal_email = serializers.EmailField(
        error_messages={
            "required": "Personal Email is required — the password setup email is sent there.",
            "blank": "Personal Email is required — the password setup email is sent there.",
        },
        help_text="The initial credentials and recovery mail go ONLY here; "
                  "it can also be used to sign in.",
    )

    def validate(self, attrs):
        """
        Email rules, per the approved policy:

        * The SAME address may serve as both Company and Personal email — an
          owner or head without a company mailbox logs in and receives the
          setup mail at one address. Not a duplicate.
        * Neither address may already belong to ANOTHER employee or login, in
          either of their email fields — checked case-insensitively with
          whitespace ignored, and refused with a message naming the field,
          BEFORE anything is created.
        """
        from django.db.models import Q

        from apps.accounts.models import User
        from apps.employees.models import Employee

        company = (attrs.get("email") or "").strip().lower()
        personal = (attrs.get("personal_email") or "").strip().lower()
        attrs["email"] = company
        attrs["personal_email"] = personal

        def taken(address: str) -> bool:
            return (
                User.objects.filter(email__iexact=address).exists()
                or Employee.objects.filter(is_active=True)
                .filter(Q(work_email__iexact=address) | Q(personal_email__iexact=address))
                .exists()
            )

        errors = {}
        if company and taken(company):
            errors["email"] = "Company Email already exists."
        # The same new address in both fields is ONE address, not a clash —
        # only check the personal side when it differs from the company side.
        if personal and personal != company and taken(personal):
            errors["personal_email"] = "Personal Email already exists."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)

    # authority
    role_code = serializers.CharField(
        max_length=50,
        help_text="Must suit the department's function and the creator's own authority.",
    )

    # placement
    department_id = serializers.UUIDField()
    #: Mandatory when a person is created through this API — the Add Employee
    #: form is the path Admin and HR use, and everybody hired that way should
    #: carry a job title. The MODEL stays nullable on purpose: existing
    #: employees predate the rule, and the recruitment conversion path can
    #: still hire from a job opening that has no designation of its own.
    designation_id = serializers.UUIDField(
        error_messages={
            "required": "Designation is required.",
            "null": "Designation is required.",
            "invalid": "Designation is required.",
        }
    )
    location_id = serializers.UUIDField(required=False, allow_null=True)
    level_id = serializers.UUIDField(required=False, allow_null=True)
    team_id = serializers.UUIDField(required=False, allow_null=True)
    reporting_manager_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="Required for everyone below department-head level.",
    )

    # employment
    employment_type = serializers.CharField(max_length=20, required=False)
    date_of_joining = serializers.DateField()
    #: The annual CTC agreed at hire. Informational — payroll's salary
    #: structures remain the authority for what is actually paid.
    annual_ctc = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
    )

    temporary_password = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        help_text=(
            "Optional. When omitted the account has no usable password and must "
            "be given one out of band, which is the safer default."
        ),
    )
