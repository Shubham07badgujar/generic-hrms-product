"""
Querysets declared on a class, built when they are used.

THE PROBLEM THIS SOLVES
-----------------------
A DRF view or serializer field commonly declares its rows in the class body:

    class AssetViewSet(ModelViewSet):
        queryset = Asset.objects.select_related("category")

That expression runs ONCE, at import. The manager's tenant predicate is read at
that moment -- with no request, so no organization -- and the queryset it
produces is then reused for the life of the process. DRF clones it per request
with `.all()`, and a clone never goes back through the manager. So a class-level
queryset is structurally outside manager-level tenancy: the predicate either
runs at import with nothing bound, or never runs at all.

That stayed invisible while the manager did not filter. Once an app became
strict, the predicate RAISED at import instead, and the first flip after
notifications took the whole URLconf down with it -- `manage.py check`, the test
collector, and a server boot would all have failed on one class attribute.
Failing closed is what exposed it.

THE SHAPE
---------
`deferred(Model)` records the same chain and replays it through
`Model._default_manager` at the moment of use:

    class AssetViewSet(ModelViewSet):
        queryset = deferred(Asset).select_related("category")

It is a `Manager`, which is what makes it drop in everywhere a class-level
queryset is read today:

* **On a class** (`ViewSet.queryset`) it returns itself. It exposes `.model` and
  evaluates nothing, which is all the router needs for a default basename and
  all `core.access.routewalk.model_of` reads.
* **On a view instance** (`self.queryset`) it builds a fresh QuerySet through the
  default manager, so the tenant predicate applies to THIS request.
  `GenericAPIView.get_queryset` and every override that reads `self.queryset`
  get a real QuerySet exactly as before.
* **As a serializer field's `queryset=`** it is stored in the field instance's
  dict, where no descriptor runs, so it stays a Manager -- and DRF's
  `RelatedField.get_queryset` explicitly accepts a Manager and calls `.all()` on
  it for every lookup.

Only an explicit allowlist of chainable methods is RECORDED. Everything else --
`all`, `get`, `count`, iteration -- falls through to the ordinary Manager proxies
and evaluates, which is correct at use time. The consequence worth knowing: an
import-time chain using an unlisted method still evaluates at import, and the
guard test in tests/tenancy/test_no_import_time_querysets.py fails on it.

WHAT THIS IS NOT
----------------
Not a way around tenancy. The replay goes through the model's default manager,
so a strict app raises when nothing is bound and filters when something is --
at use, which is the latest and therefore the safest moment to read the tenant.
"""

from __future__ import annotations

from django.db.models import Manager

#: Methods recorded rather than evaluated. Deliberately short: these are the
#: chainable, argument-only calls class-level querysets in this codebase use.
#: `all` is NOT here -- `RelatedField.get_queryset` calls `.all()` and iterates
#: the result, so it must return a real QuerySet.
RECORDED = (
    "filter",
    "exclude",
    "select_related",
    "prefetch_related",
    "order_by",
    "annotate",
    "only",
    "defer",
    "distinct",
)


class DeferredQuerySet(Manager):
    """A class-level queryset that is built through the manager at use time."""

    def __init__(self, model, steps=()):
        super().__init__()
        self.model = model
        self._steps = tuple(steps)

    def _then(self, name: str, args, kwargs) -> DeferredQuerySet:
        return type(self)(self.model, self._steps + ((name, args, kwargs),))

    def get_queryset(self):
        # Through the DEFAULT manager, never around it. This is the line that
        # makes the tenant predicate run per use rather than once per process.
        queryset = self.model._default_manager.all()
        for name, args, kwargs in self._steps:
            queryset = getattr(queryset, name)(*args, **kwargs)
        return queryset

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return self.get_queryset()

    def __repr__(self) -> str:
        chain = "".join(f".{name}(...)" for name, _, _ in self._steps)
        return f"deferred({self.model.__name__}){chain}"


def _recorder(name: str):
    def method(self, *args, **kwargs):
        return self._then(name, args, kwargs)

    method.__name__ = name
    method.__doc__ = f"Record `.{name}()` to replay at use time."
    return method


for _name in RECORDED:
    setattr(DeferredQuerySet, _name, _recorder(_name))
del _name


def deferred(model) -> DeferredQuerySet:
    """`Model.objects`, for a class body. See the module docstring."""
    return DeferredQuerySet(model)
