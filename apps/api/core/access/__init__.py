"""
Authorization.

    from core.access import can, require, scope_queryset, Resource, Action, Scope

Everything an application module needs is re-exported here. Import from
submodules only inside `core.access` itself.
"""

from .catalog import (  # noqa: F401
    READ_ACTIONS,
    USER_MANAGEMENT_RESOURCES,
    WRITE_ACTIONS,
    Action,
    DashboardKey,
    DepartmentKind,
    Layer,
    Resource,
    RoleCode,
    Scope,
)
from .context import (  # noqa: F401
    DENY_ALL,
    AccessContext,
    get_context,
    invalidate,
    resolve_context,
)
from .engine import (  # noqa: F401
    AccessDenied,
    allows,
    can,
    get_scoped_object,
    require,
    scope_queryset,
    visible_scope_label,
)
from .registry import RESOURCE_SPECS, ResourceSpec  # noqa: F401

__all__ = [
    "Action",
    "DashboardKey",
    "DepartmentKind",
    "Layer",
    "Resource",
    "RoleCode",
    "Scope",
    "READ_ACTIONS",
    "WRITE_ACTIONS",
    "USER_MANAGEMENT_RESOURCES",
    "AccessContext",
    "DENY_ALL",
    "get_context",
    "resolve_context",
    "invalidate",
    "AccessDenied",
    "can",
    "allows",
    "require",
    "scope_queryset",
    "get_scoped_object",
    "visible_scope_label",
    "RESOURCE_SPECS",
    "ResourceSpec",
]
