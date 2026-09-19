"""
META-TEST: the beat schedule and the tasks that exist agree.

The periodic tasks were previously registered by five data migrations. That
made the schedule a property of the migration history, and when this project
regenerated that history the rows vanished without a single failure --
`makemigrations` reproduces schema, never hand-written RunPython. Leave accrual
and the candidate-PII retention purge would have stopped silently.

`sync_beat_schedule` moved the schedule into a command run on every deploy.
This test guards the other half: that the command and the codebase describe the
same set of jobs. A task renamed in code but not in the schedule stops running;
a task scheduled but deleted from code fails every tick in a worker log nobody
reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.management.commands.sync_beat_schedule import SCHEDULES
from core.tasks import PER_ORGANIZATION_SUFFIXES

pytestmark = pytest.mark.meta

APPS_ROOT = Path(__file__).resolve().parents[2]
#: Both decorators that declare a Celery task in this codebase.
#: `@organization_task` wraps `@shared_task`, so a scan that looked only
#: for the latter found the seven dispatchers and none of the work they
#: dispatch -- which made every assertion below vacuously true.
DECLARES_TASK = re.compile(r'@(?:shared_task|organization_task)\(\s*name="([^"]+)"')


def _declared_task_names() -> set[str]:
    names: set[str] = set()
    for path in (APPS_ROOT / "apps").rglob("tasks.py"):
        names |= set(DECLARES_TASK.findall(path.read_text(encoding="utf-8")))
    return names


def test_the_scan_finds_the_tasks():
    """Guards the guard: an empty scan would make everything below vacuous."""
    assert len(_declared_task_names()) >= 7


def _dispatcher_of(task: str) -> str | None:
    """
    The scheduled task a per-organization subtask hangs off, or None.

    `leave.monthly_accrual.for_organization` is queued by
    `leave.monthly_accrual`, which is the name in the beat row. Deriving the
    pairing from the name rather than from a second table is what keeps the
    two from drifting: renaming the dispatcher without renaming its subtask
    breaks this immediately.
    """
    for suffix in PER_ORGANIZATION_SUFFIXES:
        if task.endswith(suffix):
            return task[: -len(suffix)]
    return None


def test_every_scheduled_task_exists_in_code():
    scheduled = {task for _, task, _ in SCHEDULES}

    missing = scheduled - _declared_task_names()

    assert not missing, (
        f"Scheduled but not defined anywhere: {sorted(missing)}. Beat will "
        f"fire these every tick and each one will fail in a worker log."
    )


#: Tasks queued by application code rather than by beat, each with the call
#: site that queues it.
#:
#: An allowlist rather than a loosened rule. "Reachable" has to keep meaning
#: something, and the failure this file exists to catch -- a task nobody ever
#: runs -- looks identical to on-demand work unless somebody says which is
#: which. Adding a name here is a claim that something calls it, and
#: `test_on_demand_tasks_have_a_caller` checks the claim.
ON_DEMAND_TASKS = {
    "imports.send_import_welcome": "apps/imports/services/employee_import.py",
}


def test_every_task_in_code_is_reachable():
    """
    A task nobody schedules never runs. That is a decision, not an accident,
    so it has to be made here rather than by omission.

    "Reachable" rather than "scheduled", for two reasons. Fanned-out work is
    deliberately NOT in the schedule: a beat row per customer would make
    provisioning write to the scheduler, so a per-organization subtask is
    reachable when the dispatcher it hangs off is scheduled, checked by name so
    renaming one without the other fails here rather than in a worker log. And
    some work is queued by the product itself when a person does something --
    those are named in ON_DEMAND_TASKS above.
    """
    scheduled = {task for _, task, _ in SCHEDULES}

    unreachable = set()
    for task in _declared_task_names():
        if task in scheduled or task in ON_DEMAND_TASKS:
            continue
        dispatcher = _dispatcher_of(task)
        if dispatcher is None or dispatcher not in scheduled:
            unreachable.add(task)

    assert not unreachable, (
        f"Defined but never reached: {sorted(unreachable)}. Add it to "
        f"SCHEDULES, give it a dispatcher that is scheduled, declare it in "
        f"ON_DEMAND_TASKS with the code that queues it, or delete it."
    )


def test_on_demand_tasks_have_a_caller():
    """
    Guards the allowlist.

    A name added here to silence the test above, for a task nothing actually
    queues, would be exactly the dead task this file exists to find -- wearing
    a label that says it is fine. So each entry names the file that queues it,
    and that file has to mention the task.
    """
    for task, caller in ON_DEMAND_TASKS.items():
        assert task in _declared_task_names(), (
            f"{task} is listed as on-demand but no code declares it."
        )
        source = (APPS_ROOT / caller).read_text(encoding="utf-8")
        attribute = task.rsplit(".", 1)[-1]
        assert attribute in source, (
            f"{caller} is named as what queues {task}, and does not mention it."
        )


def test_fanned_out_work_is_kept_out_of_the_schedule():
    """
    The other direction, and the one that matters operationally.

    A per-organization subtask in the beat schedule would fire with no
    organization argument every tick and fail every time, in a log nobody
    reads — while the dispatcher went on doing the real work, so nothing would
    look broken.
    """
    scheduled = {task for _, task, _ in SCHEDULES}

    misfiled = {task for task in scheduled if _dispatcher_of(task) is not None}

    assert not misfiled, (
        f"Fanned-out subtasks must not be scheduled directly: {sorted(misfiled)}"
    )


def test_the_fan_out_pairing_is_exercised_by_the_codebase():
    """Guards the two tests above: a naming change that silences them fails here."""
    subtasks = {t for t in _declared_task_names() if _dispatcher_of(t) is not None}

    assert len(subtasks) >= 7, (
        f"only {len(subtasks)} fanned-out subtask(s) found — the suffix "
        f"convention in core.tasks stopped matching the task names"
    )


def test_schedule_names_are_unique():
    """`update_or_create` keys on the name; a duplicate would silently win."""
    names = [name for name, _, _ in SCHEDULES]

    assert len(names) == len(set(names))
