"""
Every scheduled job runs, on a worker, with nothing bound.

This is a regression test for a real break. Flipping `imports`, `leave` and
`attendance` to filter at the manager made five of the seven beat tasks raise
`OrgContextMissing` on every tick: a worker process has no request, so nothing
binds an organization, and the tasks queried organization-owned tables
directly. No test noticed, because every task test called the task from inside
a test that already had the session organization bound -- the exact condition a
worker never has.

So this file runs each dispatcher the way beat does: by the name in the
schedule, with no organization bound, while two customers exist. The schedule
is read from `SCHEDULES` rather than listed here, so a job added next month is
covered the day it is added.
"""

from __future__ import annotations

import pytest

from config.celery import app
from core.management.commands.sync_beat_schedule import SCHEDULES

pytestmark = [pytest.mark.django_db, pytest.mark.unbound_organization]

SCHEDULED = sorted({task for _, task, _ in SCHEDULES})


@pytest.fixture(scope="module", autouse=True)
def _discovered_tasks():
    """
    Load the task modules the way a worker does, by autodiscovery.

    Nothing in the test process imports `apps/*/tasks.py` otherwise, so looking
    a scheduled name up would fail as "not registered" -- true of the test
    process, and not of the worker this file is standing in for.

    The rule is autodiscovery's own -- a `tasks` module in each installed app --
    applied directly. `app.loader.import_default_modules()` would be the literal
    call, but Celery's Django fixup closes database connections as it runs,
    which the test database guard refuses outside a test body.
    """
    import importlib
    import importlib.util

    from django.apps import apps

    for config in apps.get_app_configs():
        module = f"{config.name}.tasks"
        if importlib.util.find_spec(module) is not None:
            importlib.import_module(module)


def test_eager_task_failures_propagate():
    """
    Guards the guard.

    The dispatchers queue their subtasks with `.delay()`. If eager failures did
    not propagate, a subtask raising `OrgContextMissing` would be captured in
    its result object and every test below would pass while proving nothing.
    """
    assert app.conf.task_always_eager
    assert app.conf.task_eager_propagates


@pytest.mark.parametrize("task_name", SCHEDULED)
def test_a_scheduled_job_runs_with_no_organization_bound(task_name, org_a, org_b):
    from core.middleware import get_current_org_id

    assert get_current_org_id() is None, "the worker's condition is no binding at all"

    # A scheduled name the worker cannot find is its own failure: beat would
    # send it every tick and the worker would reject it every tick.
    assert task_name in app.tasks, f"{task_name} is scheduled but no task has that name"
    task = app.tasks[task_name]
    result = task.apply().get()

    # The dispatchers report who they queued for. Both customers must be in
    # it: a job that ran for one and silently skipped the other would not
    # raise, and would be exactly as broken.
    organizations = set(result["organizations"])
    assert str(org_a.organization.pk) in organizations
    assert str(org_b.organization.pk) in organizations

    assert get_current_org_id() is None, (
        f"{task_name} left an organization bound on the worker after it finished"
    )
