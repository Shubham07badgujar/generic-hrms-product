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

pytestmark = pytest.mark.meta

APPS_ROOT = Path(__file__).resolve().parents[2]
SHARED_TASK = re.compile(r'@shared_task\(\s*name="([^"]+)"')


def _declared_task_names() -> set[str]:
    names: set[str] = set()
    for path in (APPS_ROOT / "apps").rglob("tasks.py"):
        names |= set(SHARED_TASK.findall(path.read_text(encoding="utf-8")))
    return names


def test_the_scan_finds_the_tasks():
    """Guards the guard: an empty scan would make everything below vacuous."""
    assert len(_declared_task_names()) >= 7


def test_every_scheduled_task_exists_in_code():
    scheduled = {task for _, task, _ in SCHEDULES}

    missing = scheduled - _declared_task_names()

    assert not missing, (
        f"Scheduled but not defined anywhere: {sorted(missing)}. Beat will "
        f"fire these every tick and each one will fail in a worker log."
    )


def test_every_task_in_code_is_scheduled():
    """
    A task nobody schedules never runs. That is a decision, not an accident,
    so it has to be made here rather than by omission.
    """
    scheduled = {task for _, task, _ in SCHEDULES}

    unscheduled = _declared_task_names() - scheduled

    assert not unscheduled, (
        f"Defined but never scheduled: {sorted(unscheduled)}. Add it to "
        f"SCHEDULES, or delete the task."
    )


def test_schedule_names_are_unique():
    """`update_or_create` keys on the name; a duplicate would silently win."""
    names = [name for name, _, _ in SCHEDULES]

    assert len(names) == len(set(names))
