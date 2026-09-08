"""
Letter generation.

Letters come from TEMPLATES rendered against a context, never from strings
assembled in code. The generated PDF and the rendered HTML are both frozen onto
the letter record along with the template's name and version, so a letter
issued today stays explainable after the template is rewritten.

PDF rendering uses reportlab: pure Python, so it behaves identically on a
developer's Windows machine and in the Linux container. WeasyPrint would give
richer typography but needs GTK native libraries, which turns "generate a
letter" into an environment-specific failure.
"""

from __future__ import annotations

import html
import io
import re

from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template import Context, Template
from django.utils import timezone
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from apps.employees.models import Employee
from core.access import Action, Resource, require

from .models import EmployeeLetter, LetterStatus, LetterTemplate, LetterType


class LetterError(ValidationError):
    """A refused letter operation."""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def build_context(employee: Employee, extra: dict | None = None) -> dict:
    """
    The merge fields a template may use.

    Deliberately narrow, and deliberately free of compensation: a letter
    template is editable by HR, and putting salary in the default context would
    make it possible to leak pay into a letter type that should not carry it.
    Templates needing CTC receive it explicitly through `extra`.
    """
    from apps.organization.models import current_org_settings

    org = current_org_settings()
    today = timezone.localdate()

    context = {
        "employee_name": employee.full_name,
        "employee_code": employee.employee_code,
        "designation": employee.designation.title if employee.designation else "",
        "department": employee.department.name if employee.department else "",
        "location": employee.location.name if employee.location else "",
        "manager": employee.reporting_manager.full_name if employee.reporting_manager else "",
        "date_of_joining": employee.date_of_joining.strftime("%d %B %Y"),
        "probation_end_date": (
            employee.probation_end_date.strftime("%d %B %Y")
            if employee.probation_end_date
            else ""
        ),
        "confirmation_date": (
            employee.confirmation_date.strftime("%d %B %Y") if employee.confirmation_date else ""
        ),
        "today": today.strftime("%d %B %Y"),
        "organization_name": org.organization.name if org else "the organisation",
        "signatory_name": org.signatory_name if org else "",
        "signatory_designation": org.signatory_designation if org else "",
    }
    context.update(extra or {})
    return context


_BLOCK_SPLIT = re.compile(r"</\s*(?:p|div|h1|h2|h3|li|br)\s*>", re.IGNORECASE)
_TAG_STRIP = re.compile(r"<[^>]+>")


def _paragraphs(body_html: str) -> list[str]:
    """
    Turn the rendered HTML into paragraph text for reportlab.

    Block-level tags become paragraph breaks; inline emphasis is preserved
    because reportlab's Paragraph understands a small HTML subset (<b>, <i>,
    <u>, <br/>). Everything else is stripped rather than passed through, so a
    stray tag in a template cannot produce an unparsable PDF.
    """
    blocks = _BLOCK_SPLIT.split(body_html)
    result: list[str] = []
    for block in blocks:
        # Keep the inline tags reportlab supports; drop the rest.
        kept = re.sub(r"<(?!/?(?:b|i|u|br|super|sub)\b)[^>]*>", "", block)
        text = kept.strip()
        if text and _TAG_STRIP.sub("", text).strip():
            result.append(text)
    return result


def render_pdf(*, subject: str, body_html: str, organization: str) -> bytes:
    """Render a letter to PDF bytes."""
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=subject,
        author=organization,
    )

    styles = getSampleStyleSheet()
    letterhead = ParagraphStyle(
        "Letterhead", parent=styles["Title"], fontSize=15, leading=19, spaceAfter=2
    )
    heading = ParagraphStyle(
        "LetterSubject", parent=styles["Heading2"], fontSize=12, leading=16, spaceBefore=10
    )
    body = ParagraphStyle(
        "LetterBody",
        parent=styles["BodyText"],
        fontSize=10.5,
        leading=15.5,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    )

    story = [
        Paragraph(html.escape(organization), letterhead),
        Spacer(1, 4 * mm),
        Paragraph(html.escape(subject), heading),
        Spacer(1, 3 * mm),
    ]
    for text in _paragraphs(body_html):
        story.append(Paragraph(text, body))

    document.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def resolve_template(letter_type: str) -> LetterTemplate | None:
    return (
        LetterTemplate.objects.filter(letter_type=letter_type, is_active=True)
        .order_by("-is_default", "-version")
        .first()
    )


@transaction.atomic
def generate_letter(
    *,
    employee: Employee,
    actor,
    letter_type: str,
    template: LetterTemplate | None = None,
    context_extra: dict | None = None,
    issue: bool = True,
) -> EmployeeLetter:
    """
    Produce a letter and its PDF.

    `require` runs first, and the CONFIRMATION type carries an additional gate:
    it cannot be generated for an employee HR has not confirmed. That check
    lives here rather than only in the probation service, because a letter
    endpoint is the obvious way to reach round the back of that rule.
    """
    require(actor, Resource.LETTER, Action.CREATE)

    if letter_type not in LetterType.values:
        raise LetterError({"letter_type": f"Unknown letter type '{letter_type}'."})

    if letter_type == LetterType.CONFIRMATION:
        _assert_confirmed(employee)

    chosen = template or resolve_template(letter_type)
    if chosen is None:
        raise LetterError(
            {
                "template": (
                    f"No active template is configured for a "
                    f"{LetterType(letter_type).label.lower()}. Add one before issuing this letter."
                )
            }
        )

    context = build_context(employee, context_extra)
    subject = Template(chosen.subject).render(Context(context))
    body_html = Template(chosen.body_html).render(Context(context))

    letter = EmployeeLetter.objects.create(
        employee=employee,
        letter_type=letter_type,
        template=chosen,
        template_name=chosen.name,
        template_version=chosen.version,
        subject=subject,
        body_html=body_html,
        merge_context=context,
        generated_by=actor,
        generated_at=timezone.now(),
        status=LetterStatus.ISSUED if issue else LetterStatus.DRAFT,
        issued_at=timezone.now() if issue else None,
    )

    pdf = render_pdf(
        subject=subject, body_html=body_html, organization=context["organization_name"]
    )
    filename = f"{letter_type}-{employee.employee_code}-{timezone.localdate():%Y%m%d}.pdf"
    letter.pdf_file.save(filename, ContentFile(pdf), save=True)

    _audit_letter(letter, actor)
    return letter


def _assert_confirmed(employee: Employee) -> None:
    """
    A confirmation letter states that HR confirmed someone. If they have not,
    the letter would be a false statement of employment status.
    """
    from apps.employees.models import ProbationStatus

    if employee.probation_status != ProbationStatus.CONFIRMED or not employee.confirmation_date:
        raise LetterError(
            {
                "letter_type": (
                    "A confirmation letter can only be issued after HR has recorded a "
                    "probation confirmation for this employee. The system never confirms "
                    "an employee on its own."
                )
            }
        )


def _audit_letter(letter: EmployeeLetter, actor) -> None:
    from apps.audit.models import AuditAction, AuditLog
    from core.middleware import get_request_id

    AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        action=AuditAction.CREATE,
        resource=Resource.LETTER,
        entity_type="onboarding.EmployeeLetter",
        entity_id=str(letter.pk),
        entity_label=str(letter),
        after={
            "event": "letter_generated",
            "letter_type": letter.letter_type,
            "template": letter.template_name,
            "template_version": letter.template_version,
            "employee": letter.employee.employee_code,
        },
        request_id=get_request_id(),
    )
