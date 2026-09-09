"""
Authentication and identity serializers.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.accounts.models import User

#: After this many consecutive failures the account locks for the window below.
#: Deliberately not per-IP — an attacker rotating IPs against one account is the
#: threat, and per-account lockout is what stops it. Legitimate lockouts are
#: rare because a real user succeeds long before five tries.
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


class TokenObtainSerializer(TokenObtainPairSerializer):
    """
    Username-password login producing an access/refresh pair.

    Adds account lockout, `is_active` enforcement, and last-login stamping.
    `require_admin` (set by the /login/admin view) additionally refuses anyone
    who is not an Admin — with the SAME generic error as a bad password, so the
    endpoint reveals nothing about who holds which role.
    """

    require_admin = False

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Minimal, non-sensitive claims for the SPA to render chrome before the
        # first /me call returns. Never put permissions in the token — they must
        # be re-resolved server-side on every request, never trusted from a
        # client-held artefact.
        token["email"] = user.email
        token["name"] = user.get_full_name()
        return token

    def validate(self, attrs):
        email = (attrs.get("email") or "").strip().lower()
        password = attrs.get("password") or ""

        generic_error = serializers.ValidationError(
            {"detail": "Invalid email or password."}, code="authorization"
        )

        user = User.objects.filter(email=email).first()

        if user is None:
            # The personal email is a login identifier too: HR records both
            # addresses at hire and the setup credentials go ONLY to the
            # personal inbox — so the address that received them must open
            # the door. It resolves to the owning login only when it
            # unambiguously names ONE active employee; a shared family
            # address matching two people resolves to nobody and fails with
            # the same generic error as any unknown address, so nothing about
            # accounts is enumerable either way.
            from apps.employees.models import Employee

            matches = list(
                Employee.objects.filter(
                    personal_email__iexact=email, is_active=True, user__isnull=False,
                ).select_related("user")[:2]
            )
            if len(matches) == 1:
                user = matches[0].user
                email = user.email  # authenticate against the canonical username

        # Uniform failure whether or not the account exists — no user enumeration.
        if user is None:
            raise generic_error

        if user.is_locked:
            # Distinct message: the credentials may be right, but the door is
            # bolted. Telling the user to wait is not an enumeration risk, since
            # they already proved knowledge of the address by triggering the
            # lockout, and it prevents confused support tickets.
            remaining = int((user.locked_until - timezone.now()).total_seconds() // 60) + 1
            raise serializers.ValidationError(
                {"detail": f"Account temporarily locked. Try again in {remaining} minute(s)."},
                code="authorization",
            )

        authenticated = authenticate(
            request=self.context.get("request"), username=email, password=password
        )

        if authenticated is None:
            self._register_failure(user)
            raise generic_error

        if not authenticated.is_active:
            raise generic_error

        # Role gate for /login/admin. Checked AFTER the password so a non-admin
        # with the wrong password cannot distinguish "not an admin" from "wrong
        # password" — both are the same generic error.
        if self.require_admin and not _is_admin(authenticated):
            self._register_failure(user)
            raise generic_error

        self._register_success(authenticated)

        # Mint the pair directly rather than delegating to SimpleJWT's
        # `validate()`: that method re-runs its own authenticate() against
        # `attrs`, which we have already consumed and augmented with lockout
        # and role checks. Calling it would duplicate the work and re-open the
        # bypasses this override exists to close.
        self.user = authenticated
        refresh = self.get_token(authenticated)
        from core.access.permissions import onboarding_gate_applies

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "must_change_password": authenticated.must_change_password,
            # The SPA routes a gated new joiner straight to their onboarding.
            "onboarding_pending": onboarding_gate_applies(authenticated),
        }

    # -- lockout bookkeeping ----------------------------------------------

    @staticmethod
    def _register_failure(user: User) -> None:
        user.failed_login_count += 1
        fields = ["failed_login_count"]
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = timezone.now() + timezone.timedelta(minutes=LOCKOUT_MINUTES)
            fields.append("locked_until")
        user.save(update_fields=fields)
        _audit_login(user, success=False)

    @staticmethod
    def _register_success(user: User) -> None:
        # Reset the counter on any success, so five failures spread over weeks
        # never accumulate into a surprise lockout.
        if user.failed_login_count or user.locked_until:
            user.failed_login_count = 0
            user.locked_until = None
        user.last_login_at = timezone.now()
        user.save(update_fields=["failed_login_count", "locked_until", "last_login_at"])
        _audit_login(user, success=True)


class AdminTokenObtainSerializer(TokenObtainSerializer):
    require_admin = True


def _is_admin(user) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    from core.access.catalog import RoleCode

    return user.user_roles.filter(
        is_active=True, role__code=RoleCode.ADMIN, role__is_active=True
    ).exists()


def _audit_login(user, *, success: bool) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.access.context import _active_membership
    from core.middleware import get_request_id

    # The one audit event written BEFORE there is an access context to derive
    # an organization from -- authentication is what produces the context. So
    # it is resolved here, from the membership, or the organization's own audit
    # trail would silently lose every sign-in and failed sign-in against it.
    membership = _active_membership(user)

    AuditLog.objects.create(
        organization=membership.organization if membership else None,
        actor=user if success else None,
        actor_email=user.email,
        action=AuditAction.LOGIN if success else AuditAction.LOGIN_FAILED,
        resource="",
        entity_type="accounts.User",
        entity_id=str(user.pk),
        entity_label=user.email,
        request_id=get_request_id(),
    )


# ---------------------------------------------------------------------------
# Identity payloads
# ---------------------------------------------------------------------------


class MeSerializer(serializers.ModelSerializer):
    """The signed-in user's own identity. No permissions here — see /me/permissions."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    roles = serializers.SerializerMethodField()
    employee_id = serializers.SerializerMethodField()
    #: The linked employee's lifecycle status ("on_probation", "confirmed",
    #: …), empty for role-only logins. The SPA reads it for status-dependent
    #: framing — e.g. the leave page's probation notice.
    employee_status = serializers.SerializerMethodField()
    onboarding_pending = serializers.SerializerMethodField()
    handbook_acknowledgement_pending = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "roles",
            "employee_id",
            "employee_status",
            "must_change_password",
            "onboarding_pending",
            "handbook_acknowledgement_pending",
            "last_login_at",
        ]
        read_only_fields = fields

    def get_employee_status(self, user) -> str:
        employee = getattr(user, "employee", None)
        return employee.status if employee is not None else ""

    def get_onboarding_pending(self, user) -> bool:
        from core.access.permissions import onboarding_gate_applies

        return onboarding_gate_applies(user)

    def get_handbook_acknowledgement_pending(self, user) -> bool:
        from apps.onboarding.services import handbook_acknowledgement_pending

        return handbook_acknowledgement_pending(user)

    def get_roles(self, user) -> list[str]:
        return list(
            user.user_roles.filter(is_active=True, role__is_active=True)
            .values_list("role__code", flat=True)
        )

    def get_employee_id(self, user):
        employee = getattr(user, "employee", None)
        return str(employee.pk) if employee else None


class ChangePasswordSerializer(serializers.Serializer):
    """
    Shape only. The service re-checks the current password and runs the
    site's validators; this refuses the obviously malformed at the door.
    """

    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(
        write_only=True, trim_whitespace=False, min_length=12, max_length=128
    )
