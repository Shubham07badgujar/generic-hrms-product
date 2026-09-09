"""
Identity and RBAC.

    User            login identity (email-based)
    Role            a named bundle of authority, with structural capability flags
    RolePermission  (role, resource, action) -> scope.  Absence == deny.
    UserRole        a grant of a role to a user
    UserPermissionOverride   per-user exception; widens OR explicitly denies

WHY FLAGS AND NOT JUST MATRIX ROWS
----------------------------------
`is_read_only`, `can_manage_users` and `requires_employee` are columns on Role,
not rows in the permission matrix. They are *structural* — the engine applies
them as clamps after the matrix has been read, so a mistake while editing
permissions can never grant a CEO a write or hand account control to a role
that should not have it. A matrix row is data; a flag is an invariant.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.access.catalog import Action, DashboardKey, DepartmentKind, Layer, Resource, Scope
from core.models import OrgOwnedModel


class UserManager(BaseUserManager):
    """Email-based manager. There are no usernames in this system."""

    use_in_migrations = True

    def normalize_email(self, email):
        """
        Lower-case the whole address.

        Postgres string comparison is case-sensitive and email is our
        USERNAME_FIELD, so the stored value and the login lookup must agree.
        RFC 5321 permits a case-sensitive local part, but no real mail system
        relies on it.
        """
        return (email or "").strip().lower()

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra)
        if password:
            user.set_password(password)
        else:
            # Bootstrap and HR-created accounts get their password set out of
            # band; an unusable password is safer than a blank one.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser must have is_staff and is_superuser set.")
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Login identity.

    Deliberately thin. Everything about the *person* lives on Employee; this
    model is only about authentication. Admin and CEO have a User with no
    Employee, which is why `requires_employee` exists on Role.
    """

    id = models.UUIDField(primary_key=True, default=__import__("uuid").uuid4, editable=False)

    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(
        default=False,
        help_text="Django admin access. Reserved for platform operators — "
        "the HRMS itself is administered through the API, not Django admin.",
    )

    must_change_password = models.BooleanField(
        default=False,
        help_text="Set when HR provisions an account with a temporary password.",
    )
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    mfa_secret = models.CharField(max_length=64, null=True, blank=True)

    #: Operator of the SaaS platform, not a customer's employee.
    #:
    #: A structural flag rather than a Role, for the same reason `is_read_only`
    #: and `can_manage_users` are columns: Role rows are runtime-editable, so a
    #: platform capability expressed as a role could be granted by a customer's
    #: own administrator. This is set only by `bootstrap_platform_admin`; no API
    #: writes it, which is why it is not editable.
    #:
    #: A platform admin belongs to NO organization and holds NO role, so
    #: `resolve_context()` already yields them no grants over customer data.
    #: The flag selects a different surface; it does not widen this one.
    is_platform_admin = models.BooleanField(default=False, editable=False)

    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        # Canonicalise on every write path — admin, shell, imports, direct
        # assignment — so the stored value always matches the login lookup.
        if self.email:
            lowered = self.email.strip().lower()
            if lowered != self.email:
                self.email = lowered
                update_fields = kwargs.get("update_fields")
                if update_fields is not None and "email" not in update_fields:
                    kwargs["update_fields"] = list(update_fields) + ["email"]
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_short_name(self) -> str:
        return self.first_name or self.email.split("@")[0]

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > timezone.now()


class Role(OrgOwnedModel):
    """
    A named bundle of authority.

    Roles are seeded (`manage.py seed_roles`) rather than user-created: the
    18 roles map to a real organizational hierarchy, and letting them be
    invented ad hoc is how permission models rot. `name` is editable so the
    organization can relabel; `code` is the stable machine key and is not.
    """

    code = models.SlugField(max_length=50, unique=True, editable=False)
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)

    layer = models.PositiveSmallIntegerField(choices=Layer.choices, db_index=True)

    # --- structural capability flags (clamps, not matrix rows) -------------
    is_read_only = models.BooleanField(
        default=False,
        help_text=(
            "Principal may never mutate anything. Applied as a clamp AFTER the "
            "permission matrix and any per-user override, so no data change can "
            "grant a write. Also blocked at the middleware layer."
        ),
    )
    can_manage_users = models.BooleanField(
        default=False,
        help_text=(
            "Prerequisite for any USER or ROLE permission. Without it, those "
            "resources are stripped from the resolved grants regardless of what "
            "the matrix says."
        ),
    )
    requires_employee = models.BooleanField(
        default=True,
        help_text=(
            "If set, a holder with no linked Employee resolves to no access at "
            "all. False only for org-level roles (admin, ceo) which must work "
            "before any Employee records exist."
        ),
    )
    is_grantable = models.BooleanField(
        default=True,
        help_text="False means the role can only be assigned by bootstrap (admin).",
    )
    is_system = models.BooleanField(
        default=True,
        help_text="Seeded canonical role. code/layer/flags are immutable.",
    )

    # --- presentation hints (never authorization) -------------------------
    department_kind = models.CharField(
        max_length=20, choices=DepartmentKind.choices, blank=True
    )
    dashboard_key = models.CharField(
        max_length=40, choices=DashboardKey.choices, default=DashboardKey.SELF
    )

    max_seats = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Cap on active grants. NULL = unlimited."
    )

    class Meta:
        ordering = ["layer", "name"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(is_read_only=True, can_manage_users=True),
                name="ck_role_readonly_cannot_manage_users",
            ),
        ]
        indexes = [models.Index(fields=["layer", "is_active"])]

    def __str__(self) -> str:
        return self.name


class RolePermission(OrgOwnedModel):
    """
    One cell of the permission matrix: `(role, resource, action) -> scope`.

    Only non-NONE rows are stored — absence means deny. That keeps the table
    at roughly 250 rows instead of 18 roles x 39 resources x 10 actions, and
    makes "what can this role do?" answerable by reading its rows rather than
    filtering out thousands of zeroes.
    """

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    resource = models.CharField(max_length=40, choices=Resource.choices, db_index=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    scope = models.PositiveSmallIntegerField(choices=Scope.choices, default=Scope.NONE)

    is_customized = models.BooleanField(
        default=False,
        help_text=(
            "Set when an administrator edits this cell. The seeder then leaves "
            "it alone, so re-running seed_roles never silently reverts a "
            "deliberate change."
        ),
    )

    class Meta:
        ordering = ["role__layer", "resource", "action"]
        constraints = [
            models.UniqueConstraint(
                fields=["role", "resource", "action"], name="uniq_roleperm_role_res_act"
            ),
        ]
        indexes = [models.Index(fields=["role", "resource"])]

    def __str__(self) -> str:
        return f"{self.role.code}: {self.resource}.{self.action} = {Scope(self.scope).name}"

    def clean(self):
        super().clean()
        if self.scope == Scope.NONE:
            return

        if self.resource in {Resource.USER, Resource.ROLE} and not self.role.can_manage_users:
            raise ValidationError(
                {
                    "resource": (
                        f"Role '{self.role.code}' is not flagged can_manage_users, so it "
                        f"cannot hold '{self.resource}' permissions. Grant the capability "
                        f"on the role first."
                    )
                }
            )

        from core.access.catalog import WRITE_ACTIONS

        if self.role.is_read_only and self.action in WRITE_ACTIONS:
            raise ValidationError(
                {
                    "action": (
                        f"Role '{self.role.code}' is read-only; write actions cannot be "
                        f"granted to it."
                    )
                }
            )


class UserRole(OrgOwnedModel):
    """A grant of a role to a user."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="grants")
    assigned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["user__email", "role__layer"]
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="uniq_userrole_user_role"),
        ]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user.email} -> {self.role.code}"

    def clean(self):
        """
        Enforce read-only exclusivity.

        A read-only role (CEO) cannot be combined with any other role. This is
        what makes `AccessContext.read_only` unambiguous: without it, a CEO who
        also held an operational role would have contradictory authority and
        the clamp would silently strip the operational role's writes, which is
        confusing rather than correct.
        """
        super().clean()
        if not self.is_active or self.role_id is None or self.user_id is None:
            return

        siblings = UserRole.objects.filter(user_id=self.user_id, is_active=True)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)

        if self.role.is_read_only and siblings.exists():
            raise ValidationError(
                f"'{self.role.name}' is a view-only role and cannot be combined with "
                f"any other role. Revoke the existing roles first."
            )
        if siblings.filter(role__is_read_only=True).exists():
            raise ValidationError(
                f"This user holds a view-only role, which cannot be combined with "
                f"'{self.role.name}'."
            )


class UserPermissionOverride(OrgOwnedModel):
    """
    A per-user exception to the role-derived permission.

    This is the "unless explicitly authorized" escape hatch the hierarchy
    requires — e.g. delegating candidate management to one specific HR Manager
    without changing what every HR Manager can do.

    An override REPLACES the role-derived scope for that (resource, action).
    It can widen or explicitly deny (scope=NONE). It cannot defeat the
    read-only clamp or the user-management gate, both of which run afterwards.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="permission_overrides"
    )
    resource = models.CharField(max_length=40, choices=Resource.choices)
    action = models.CharField(max_length=20, choices=Action.choices)
    scope = models.PositiveSmallIntegerField(choices=Scope.choices)

    reason = models.CharField(
        max_length=255,
        help_text="Why this exception exists. Required — an unexplained "
        "permission grant is impossible to review later.",
    )
    granted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="NULL = permanent. Prefer an expiry."
    )

    class Meta:
        ordering = ["user__email", "resource", "action"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "resource", "action"], name="uniq_override_user_res_act"
            ),
        ]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self) -> str:
        return f"{self.user.email}: {self.resource}.{self.action} = {Scope(self.scope).name}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()
