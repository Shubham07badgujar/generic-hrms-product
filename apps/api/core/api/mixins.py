"""
Shared viewset mixins.

`ServiceCreatedOnly` exists because of a combination that is easy to build by
accident and hard to see afterwards.

Several resources here are never created by a client. A probation review is
opened by the nightly sweep; onboarding items are copied from a template when
someone is hired; clearance items are issued when an exit starts. Their
serializers are therefore entirely read-only — correct, and the reason the
problem hides: a serializer with no writable field VALIDATES AN EMPTY BODY
happily, because there is nothing to validate.

If the viewset still routes `create`, DRF then calls `Model.objects.create()`
with no fields at all. Postgres raises a not-null violation, Django returns
500, and the response carries the constraint name and a row dump.

So: an unvalidated write path AND an error that describes the schema, from a
viewset nobody intended to be writable.

Closing the method is the fix. It has to be explicit rather than dropping
`post` from `http_method_names`, because these viewsets legitimately need POST
for their custom `@action` routes — completing a clearance item, deciding a
probation review. Removing the method would take those with it.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response


class ServiceCreatedOnly:
    """
    Refuses `POST` on the list route while leaving `@action` routes intact.

    Mix in ahead of the viewset base class.
    """

    #: Read by the cutover write-surface test, so the guard can tell a closed
    #: create apart from one nobody has noticed yet.
    _closed_writes = ("create",)

    def create(self, request, *args, **kwargs):
        return Response(
            {
                "error": {
                    "code": "method_not_allowed",
                    "message": (
                        f"{self.__class__.__name__.replace('ViewSet', '')} records are "
                        f"created by the workflow that owns them, not directly. "
                        f"There is no API path that would produce a valid one."
                    ),
                }
            },
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )
