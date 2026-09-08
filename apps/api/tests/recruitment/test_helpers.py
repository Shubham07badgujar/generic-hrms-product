"""Shared plumbing for the recruitment tests. Not a test module."""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

_slot = [timezone.now() + dt.timedelta(days=10)]


def next_slot() -> dt.datetime:
    """A fresh, non-overlapping interview time on every call."""
    _slot[0] = _slot[0] + dt.timedelta(hours=3)
    return _slot[0]


def complete_interview_for(application, order: int, role_code: str, staff, *,
                           recommendation: str = "hire"):
    """Schedule an interview at `order` and submit passing feedback for it."""
    from apps.recruitment.services.interviews import schedule_interview, submit_feedback

    stage = application.job_opening.workflow.stages.get(order=order)
    interviewer = staff[role_code]

    interview = schedule_interview(
        application=application,
        stage=stage,
        interviewer=interviewer,
        actor=staff["hr_head"].user,
        scheduled_at=next_slot(),
    )
    answers = {
        field.key: (
            3 if field.kind == "rating_1_5"
            else True if field.kind == "boolean"
            else "ok"
        )
        for field in stage.feedback_form.fields.filter(is_required=True)
    }
    submit_feedback(
        interview=interview,
        actor=interviewer.user,
        answers=answers,
        recommendation=recommendation,
        strengths="Capable",
        concerns="None material",
        overall_rating=4,
    )
    return interview
