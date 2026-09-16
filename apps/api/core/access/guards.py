"""
An object handed to a service belongs to the organization acting on it.

WHY THIS EXISTS AS WELL AS THE SERIALIZER SCOPE
-----------------------------------------------
Services in this codebase take model instances, not ids: `start_exit(employee=
...)`. They check that the ACTOR holds a permission -- `require(actor,
Resource.OFFBOARDING, Action.CREATE)` -- and then act on whatever object they
were given. Holding OFFBOARDING/CREATE in your own company says nothing about
whether the employee is in it.

That gap was exploited over HTTP: a request body named another company's
employee, the serializer resolved it across the tenant boundary, and
`start_exit` offboarded them. The serializer layer is now scoped, which closes
the HTTP route. It does not close every route. Anything else that constructs
the object and calls the service -- a Celery task, a management command,
another service -- reaches the same code with nothing in between.

So the services that change another record's state assert ownership
themselves. This is defence in depth rather than the primary control, and it is
cheap: one comparison against the bound organization.

THE FAILURE SHAPE
-----------------
`Http404`, which the API's exception handler turns into a 404. Deliberately not
403, and deliberately the same response a nonexistent id gets: a refusal that
confirmed the row exists in some other company would itself be a disclosure.
Outside a request it is simply an exception, which fails the task loudly.

Nothing bound is a refusal too. A caller that cannot say which organization it
is acting for cannot show the object is in scope.
"""

from __future__ import annotations

import logging

from django.http import Http404

logger = logging.getLogger("hrms.access")


def require_same_organization(*objects) -> None:
    """
    Refuse unless every non-None object belongs to the bound organization.

    Logs identifiers only -- the model, the primary key and the two
    organization ids -- never a name or any other field of the row.
    """
    from core.middleware import get_current_org_id

    organization_id = get_current_org_id()
    for obj in objects:
        if obj is None:
            continue
        owner = getattr(obj, "organization_id", None)
        if organization_id is not None and owner == organization_id:
            continue
        logger.error(
            "access.cross_tenant_object_refused model=%s pk=%s bound=%s owner=%s",
            getattr(getattr(obj, "_meta", None), "label", type(obj).__name__),
            getattr(obj, "pk", None),
            organization_id,
            owner,
        )
        raise Http404
