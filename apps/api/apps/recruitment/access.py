"""
Object-level authorization for the hiring pipeline.

WHY THIS IS NOT IN `core.access`
--------------------------------
The generic engine narrows a queryset by walking a path to the owning Employee.
Recruitment rows have no owning employee: a candidate belongs to an application,
an application to a job opening, and a job opening to a department. Those
resources are registered `person_scoped=False` precisely so the generic scoper
declines to guess, and `core/access/registry.py` says as much — the paths are
recruitment schema knowledge and belong here.

WHY `get_scoped_object` COULD NOT BE USED
-----------------------------------------
`core.access.get_scoped_object` looks like the right tool and is not. It
delegates to `scope_queryset()`, which returns `none()` for any
`person_scoped=False` resource below `Scope.ALL` — so a department head asking
about a job opening in their own department gets a 404. Its zero callers are
not an oversight.

THE GAP THIS CLOSES
-------------------
`ApplicationCreateSerializer` used to validate only that the job's workflow was
published. It never asked whether the caller was entitled to use that job
opening at all. That was unexploitable only because no role below `Scope.ALL`
holds `APPLICATION/CREATE` — an accident of the permission matrix, not a
property of the code, and exactly the kind of thing that stops being true the
day someone grants a department head that permission.

Both the list filter and the single-object check now come from
`pipeline_scope_filter`, so they cannot drift apart. That drift is what produced
the gap in the first place.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet
from django.http import Http404

from core.access import Action, Resource, Scope, can, get_context


def pipeline_scope_filter(
    qs: QuerySet,
    user,
    *,
    resource: str,
    department_path: str,
    assigned_path: str | tuple[str, ...] | None,
    action: str = Action.VIEW,
) -> QuerySet:
    """
    Narrow a recruitment queryset to what `user` may see.

      ALL         → everything (HR, Admin, CEO)
      DEPARTMENT  → rows whose job sits in the caller's department closure
      TEAM/SELF   → rows the caller is personally assigned to

    TEAM and SELF collapse to the same thing: a hiring pipeline has no reporting
    tree, so "my team's candidates" adds nothing over "candidates assigned to
    me". `assigned_path=None` means SELF is not expressible for that model and
    resolves to nothing, which is the safe direction. A model may be assigned
    through more than one relation — a job belongs to its recruiter AND to the
    interviewers booked on its applications — so a tuple of paths ORs together.

    DEPARTMENT is a UNION, not a replacement: the caller's department PLUS
    whatever they are personally assigned to. Scopes merge by breadth, so a
    department head who also interviews holds DEPARTMENT where a pure
    interviewer holds SELF — and filtering on the department alone silently
    took away the rows they were assigned to outside it. A Medical Director
    booked on a Sales round could open the interview and file the feedback,
    then get a 404 recording the decision, because the application was not
    "hers". Widening someone's authority must never subtract their own work.
    """
    scope = can(user, resource, action)

    if not scope:
        return qs.none()
    if scope == Scope.ALL:
        return qs

    ctx = get_context(user)

    paths = ()
    if assigned_path is not None and ctx.employee_id is not None:
        paths = (assigned_path,) if isinstance(assigned_path, str) else assigned_path

    assigned = Q()
    for path in paths:
        assigned |= Q(**{path: ctx.employee_id})

    if scope == Scope.DEPARTMENT:
        visible = Q()
        if ctx.department_ids:
            visible |= Q(**{department_path: ctx.department_ids})
        visible |= assigned
        if not visible:
            return qs.none()
        return qs.filter(visible).distinct()

    if not paths:
        return qs.none()
    return qs.filter(assigned).distinct()


def resolve_job_opening(job_opening, *, user, action: str = Action.VIEW):
    """
    Return `job_opening` if the caller may operate on it; raise Http404 if not.

    404 rather than 403, for the reason `core.access.get_scoped_object` gives:
    a 403 confirms the row exists, which turns the endpoint into an enumeration
    oracle over the hiring pipeline.

    `action` defaults to VIEW deliberately. The question being asked is "is this
    job inside the pipeline you are entitled to operate on", not "may you author
    job openings" — a department head holds `JOB_OPENING/VIEW` at DEPARTMENT and
    no CREATE at all, so gating on CREATE would 404 the very person the check
    exists to admit. This mirrors `ScopedQuerysetMixin.scoping_action()`, which
    scopes custom actions by VIEW for the same reason. The authority to *write*
    is a separate `require()` in the calling service.
    """
    from apps.recruitment.models import JobOpening

    permitted = pipeline_scope_filter(
        JobOpening.objects.filter(pk=job_opening.pk),
        user,
        resource=Resource.JOB_OPENING,
        department_path="department_id__in",
        assigned_path=("recruiter_id", "applications__interviews__interviewer_id"),
        action=action,
    )
    resolved = permitted.first()
    if resolved is None:
        raise Http404("No such job opening.")
    return resolved
