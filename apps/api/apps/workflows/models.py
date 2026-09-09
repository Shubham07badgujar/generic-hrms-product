"""
The configurable hiring workflow engine — CONFIGURATION.

A workflow is DATA, never code. There is deliberately no branch anywhere in
this system on a job title: Therapist and Office Boy are two rows in
`HiringWorkflow` with different `WorkflowStage` children, executed by the same
engine. Adding a Nurse or Sales Executive pipeline is a configuration task.

    HiringWorkflow
      └─ WorkflowStage (ordered)          who acts, what kind of act, what is required
           ├─ StageTransition             which decision leads where
           └─ FeedbackForm → FeedbackField   the structured assessment captured
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.access.catalog import DepartmentKind
from core.models import OrgOwnedModel


class StageKind(models.TextChoices):
    """
    What KIND of act a stage represents.

    The engine dispatches on this — not on job title — so a stage's behaviour
    is determined by its type and its configuration flags alone.
    """

    APPLICATION = "application", "Application received"
    HR_VERIFICATION = "hr_verification", "HR verification"
    INTERVIEW = "interview", "Interview"
    DEPARTMENT_DECISION = "department_decision", "Department recommendation"
    HR_FINAL_DECISION = "hr_final_decision", "HR Head final decision"
    OFFER = "offer", "Offer"
    ONBOARDING = "onboarding", "Onboarding"
    TERMINAL = "terminal", "Terminal"


class Decision(models.TextChoices):
    """
    Every decision a stage may permit.

    Deliberately distinguishes RECOMMEND_* (advisory, made by departments) from
    SELECT/REJECT (terminal, HR Head only). Collapsing them would let an
    interviewer's opinion end a candidacy, which the approved hierarchy forbids.
    """

    # Progression
    PASS = "pass", "Pass — advance"
    VERIFY = "verify", "Verified"
    REQUEST_INFO = "request_info", "Request more information"
    #: The screening "no": the stage's own role (the Recruiter, at
    #: verification) declines an application that should go no further —
    #: incomplete, ineligible, not what the posting asked for. Closes the
    #: application as rejected with a mandatory reason and the rejection
    #: email, but is NOT the HR Head's terminal REJECT: that authority, and
    #: its permission, are untouched. It is authorised as the stage's own
    #: write (APPLICATION/EDIT).
    SCREEN_OUT = "screen_out", "Reject application"

    # Advisory, from a department
    RECOMMEND_SELECT = "recommend_select", "Recommend selection"
    RECOMMEND_REJECT = "recommend_reject", "Recommend rejection"

    # Terminal — HR Head only
    SELECT = "select", "Select"
    REJECT = "reject", "Reject"

    # Offer lifecycle
    OFFER_ACCEPTED = "offer_accepted", "Offer accepted"
    OFFER_DECLINED = "offer_declined", "Offer declined"

    WITHDRAW = "withdraw", "Candidate withdrew"


#: Decisions only the final HR decision stage may carry. Enforced in
#: `WorkflowStage.clean()`, so a misconfigured workflow cannot grant an
#: interviewer terminal authority.
TERMINAL_DECISIONS = frozenset({Decision.SELECT, Decision.REJECT})

#: Advisory decisions. A department stage may recommend rejection; it can never
#: reject.
ADVISORY_DECISIONS = frozenset({Decision.RECOMMEND_SELECT, Decision.RECOMMEND_REJECT})


class HiringWorkflow(OrgOwnedModel):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    department_kind = models.CharField(
        max_length=20,
        choices=DepartmentKind.choices,
        blank=True,
        help_text="Function this workflow serves. Blank = any.",
    )
    is_default = models.BooleanField(default=False)
    is_published = models.BooleanField(
        default=False,
        help_text="Only a published workflow may be attached to a job opening. "
        "Drafts can be edited freely; publishing freezes the shape.",
    )
    version = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"

    @property
    def first_stage(self):
        return self.stages.filter(is_active=True).order_by("order").first()

    def clean(self):
        super().clean()
        if self.is_published and self.pk and not self.stages.filter(is_active=True).exists():
            raise ValidationError({"is_published": "A workflow needs stages before publishing."})


class WorkflowStage(OrgOwnedModel):
    """
    One step. Everything the engine needs to run it is a field here.

    `responsible_role` is who may act. `requires_interview` and
    `requires_feedback` are preconditions the engine enforces before accepting
    a decision. `allowed_decisions` bounds what may be recorded.
    """

    workflow = models.ForeignKey(HiringWorkflow, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=30, choices=StageKind.choices)

    responsible_role = models.ForeignKey(
        "accounts.Role",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="workflow_stages",
        help_text="Role permitted to act. Null for automatic stages.",
    )

    allowed_decisions = models.JSONField(
        default=list, help_text="Decision values this stage may record."
    )

    requires_interview = models.BooleanField(default=False)
    requires_feedback = models.BooleanField(default=False)
    feedback_form = models.ForeignKey(
        "workflows.FeedbackForm",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stages",
    )

    is_mandatory = models.BooleanField(default=True)
    can_skip = models.BooleanField(default=False)
    is_final_hr_decision = models.BooleanField(
        default=False,
        help_text="This stage carries terminal SELECT/REJECT authority. "
        "Exactly one per workflow.",
    )
    is_terminal = models.BooleanField(default=False)
    is_won = models.BooleanField(default=False)

    sla_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["workflow", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "order"], name="uniq_stage_order_per_workflow"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.workflow.name} · {self.order}. {self.name}"

    def clean(self):
        super().clean()

        invalid = set(self.allowed_decisions) - set(Decision.values)
        if invalid:
            raise ValidationError(
                {"allowed_decisions": f"Unknown decisions: {sorted(invalid)}"}
            )

        # A stage may only carry terminal authority if it is THE final HR
        # decision stage. This is the configuration-level guarantee that an
        # interview stage can never be given the power to reject.
        terminal = set(self.allowed_decisions) & TERMINAL_DECISIONS
        if terminal and not self.is_final_hr_decision:
            raise ValidationError(
                {
                    "allowed_decisions": (
                        f"{sorted(terminal)} are terminal decisions reserved for the "
                        f"final HR decision stage. Department stages may only "
                        f"recommend. Set is_final_hr_decision, or use "
                        f"recommend_select / recommend_reject."
                    )
                }
            )

        if self.requires_feedback and not self.feedback_form_id:
            raise ValidationError(
                {"feedback_form": "A stage requiring feedback must name a form."}
            )

        if self.can_skip and self.is_mandatory:
            raise ValidationError(
                {"can_skip": "A mandatory stage cannot also be skippable."}
            )


class StageTransition(OrgOwnedModel):
    """
    `(from_stage, decision) -> to_stage`.

    The transition table IS the workflow's control flow. Because it is data,
    the engine never needs to know which pipeline it is executing: it looks up
    the row and moves.
    """

    from_stage = models.ForeignKey(
        WorkflowStage, on_delete=models.CASCADE, related_name="outgoing_transitions"
    )
    on_decision = models.CharField(max_length=30, choices=Decision.choices)
    to_stage = models.ForeignKey(
        WorkflowStage, on_delete=models.CASCADE, related_name="incoming_transitions"
    )

    class Meta:
        ordering = ["from_stage__order"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_stage", "on_decision"], name="uniq_transition_per_decision"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.from_stage.name} --{self.on_decision}--> {self.to_stage.name}"

    def clean(self):
        super().clean()
        if self.from_stage_id and self.to_stage_id:
            if self.from_stage.workflow_id != self.to_stage.workflow_id:
                raise ValidationError(
                    {"to_stage": "A transition cannot cross workflows."}
                )
            if self.from_stage_id == self.to_stage_id:
                raise ValidationError({"to_stage": "A stage cannot transition to itself."})


class FeedbackForm(OrgOwnedModel):
    """A structured assessment captured at an interview stage."""

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class FeedbackFieldKind(models.TextChoices):
    RATING_1_5 = "rating_1_5", "Rating (1-5)"
    TEXT = "text", "Free text"
    BOOLEAN = "boolean", "Yes / No"
    CHOICE = "choice", "Single choice"


class FeedbackField(OrgOwnedModel):
    form = models.ForeignKey(FeedbackForm, on_delete=models.CASCADE, related_name="fields")
    key = models.SlugField(max_length=50)
    label = models.CharField(max_length=200)
    help_text = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=20, choices=FeedbackFieldKind.choices)
    choices = models.JSONField(default=list, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_required = models.BooleanField(default=True)

    class Meta:
        ordering = ["form", "order"]
        constraints = [
            models.UniqueConstraint(fields=["form", "key"], name="uniq_field_key_per_form")
        ]

    def __str__(self) -> str:
        return f"{self.form.name} · {self.label}"
