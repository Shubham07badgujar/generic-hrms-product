"""
Role and permission seeding.

Idempotent: safe to run on every deploy. Permission cells an administrator has
edited (`is_customized=True`) are never overwritten, so re-seeding cannot
silently revert a deliberate policy change — a defect in the previous system,
where seeding used get_or_create and so never propagated *any* update to
existing installations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from apps.accounts.models import Role, RolePermission
from apps.accounts.permission_matrix import ROLE_SPECS

logger = logging.getLogger("hrms.access")


@dataclass
class SeedResult:
    roles_created: int = 0
    roles_updated: int = 0
    permissions_created: int = 0
    permissions_updated: int = 0
    permissions_removed: int = 0
    permissions_skipped_customized: int = 0

    def __str__(self) -> str:
        return (
            f"roles: +{self.roles_created} ~{self.roles_updated} | "
            f"permissions: +{self.permissions_created} ~{self.permissions_updated} "
            f"-{self.permissions_removed} (skipped {self.permissions_skipped_customized} customized)"
        )


@transaction.atomic
def seed_roles(*, organization, prune: bool = True) -> SeedResult:
    """
    Create or update one organization's 18 canonical roles and their matrix.

    The organization is REQUIRED, and the lookup below is scoped to it. Without
    that scoping `update_or_create(code=...)` matches on a code that is now
    unique only per organization, so seeding a second company would find the
    FIRST company's role, rewrite it, and leave the second with no roles at all
    -- silently, since update_or_create reports success either way.

    `prune=False` leaves permission rows that are no longer in the matrix in
    place. Use it when narrowing the matrix and you want to inspect what would
    be removed before removing it.
    """
    result = SeedResult()

    for spec in ROLE_SPECS:
        role, created = Role.objects.update_or_create(
            organization=organization,
            code=spec.code,
            defaults={
                "name": spec.name,
                "description": spec.description,
                "layer": spec.layer,
                "is_read_only": spec.is_read_only,
                "can_manage_users": spec.can_manage_users,
                "requires_employee": spec.requires_employee,
                "is_grantable": spec.is_grantable,
                "is_system": True,
                "department_kind": spec.department_kind,
                "dashboard_key": spec.dashboard_key,
                "max_seats": spec.max_seats,
                "is_active": True,
            },
        )
        if created:
            result.roles_created += 1
        else:
            result.roles_updated += 1

        _sync_permissions(role, spec, result, prune=prune)

    logger.info("access.roles_seeded %s", result)
    return result


def _sync_permissions(role, spec, result: SeedResult, *, prune: bool) -> None:
    existing = {
        (perm.resource, perm.action): perm
        for perm in RolePermission.objects.filter(role=role)
    }
    desired: set[tuple[str, str]] = set()

    for resource, actions in spec.permissions.items():
        for action, scope in actions.items():
            key = (str(resource), str(action))
            desired.add(key)
            perm = existing.get(key)

            if perm is None:
                RolePermission.objects.create(
                    role=role, resource=str(resource), action=str(action), scope=scope
                )
                result.permissions_created += 1
                continue

            # An administrator deliberately changed this cell — leave it.
            if perm.is_customized:
                result.permissions_skipped_customized += 1
                continue

            if perm.scope != scope or not perm.is_active:
                perm.scope = scope
                perm.is_active = True
                perm.save(update_fields=["scope", "is_active", "updated_at"])
                result.permissions_updated += 1

    if not prune:
        return

    for key, perm in existing.items():
        if key in desired:
            continue
        if perm.is_customized:
            # A cell added by an administrator that the matrix does not define.
            # Removing it would silently revoke a deliberate grant.
            result.permissions_skipped_customized += 1
            continue
        perm.delete(hard=True)
        result.permissions_removed += 1


def matrix_report() -> list[dict]:
    """
    Flatten the seeded matrix for review or export.

    Reads the DATABASE, not the spec file, so it reflects any administrator
    customisation — which is what a reviewer actually needs to see.
    """
    from core.access.catalog import Scope

    rows = []
    for perm in (
        RolePermission.objects.filter(is_active=True)
        .select_related("role")
        .order_by("role__layer", "role__name", "resource", "action")
    ):
        rows.append(
            {
                "role": perm.role.name,
                "role_code": perm.role.code,
                "layer": perm.role.layer,
                "resource": perm.resource,
                "action": perm.action,
                "scope": Scope(perm.scope).name,
                "customized": perm.is_customized,
            }
        )
    return rows
