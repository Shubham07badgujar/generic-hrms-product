"""
The offer letter, as a PDF the candidate keeps.

Generated entirely from stored data — the offer row, its application and the
organisation's settings. Nothing about a candidate or an offer is written
here; change the offer and the letter changes, change the letterhead in
settings and every FUTURE letter changes while already-sent ones stay frozen
on the Offer row.

Layout: letterhead (logo, organisation, branch strip) → date and reference →
addressee → subject → appointment paragraph → employment details table →
terms → signature block (signature image over the signatory's name, from
settings). The whole page uses the letterhead's deep blue for structure so
the letter visibly belongs to the brand.
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

#: The letterhead's own deep blue (sampled from the logo).
BRAND = colors.HexColor("#232B7C")
INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5a5a5a")


def _org():
    from apps.organization.models import OrgSettings

    return OrgSettings.objects.first()


def _branch_strip() -> str:
    from apps.organization.models import Location

    names = list(
        Location.objects.filter(is_active=True)
        .order_by("name")
        .values_list("name", flat=True)
    )
    # "<Name> Branch" reads better as just "<Name>" on a strip.
    short = [n.replace(" Branch", "").replace(" Clinic", "") for n in names]
    return "  |  ".join(short)


def _inr(amount) -> str:
    """INR 12,34,567.00 — Indian grouping; "INR" because the base PDF fonts
    have no rupee glyph and a missing-glyph box on a CTC is unacceptable."""
    from decimal import Decimal

    amount = Decimal(str(amount))
    whole, _, frac = f"{amount:.2f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}INR {whole}.{frac}"


def _scaled_image(field_file, *, max_width, max_height) -> Image | None:
    """
    An Image flowable that keeps its aspect ratio, or None.

    The source is RESAMPLED down to at most 3x its display size before being
    embedded. A letterhead uploaded at camera resolution would otherwise be
    embedded whole — a 3 MB letter that a slow SMTP link cannot upload inside
    the mail timeout — while 3x display size stays print-crisp at ~100 KB.
    """
    if not field_file:
        return None
    try:
        data = field_file.read()
        reader = ImageReader(io.BytesIO(data))
        width, height = reader.getSize()
        scale = min(max_width / width, max_height / height, 1)

        target_px = (int(width * scale * 3 / 0.75), int(height * scale * 3 / 0.75))
        if width > target_px[0]:
            from PIL import Image as PILImage

            with PILImage.open(io.BytesIO(data)) as source:
                resampled = source.convert("RGBA").resize(
                    target_px, PILImage.LANCZOS
                )
                buffer = io.BytesIO()
                resampled.save(buffer, "PNG", optimize=True)
                data = buffer.getvalue()

        return Image(io.BytesIO(data), width=width * scale, height=height * scale)
    except Exception:  # noqa: BLE001 — a broken image must not block an offer
        return None



#: The logo's lime green — the frame and table accents, per HR's chosen look.
GREEN = colors.HexColor("#A6CE39")
GREEN_TINT = colors.HexColor("#f3f8e4")

#: Annual CTC STRICTLY ABOVE this includes the Security Deposit Policy
#: clause; at or below it the clause is omitted. HR never chooses a letter
#: version — the stored CTC decides, in this one place, for preview,
#: download and the emailed attachment alike.
SECURITY_DEPOSIT_CTC_ABOVE = Decimal("240000")


def includes_security_deposit(ctc) -> bool:
    return Decimal(str(ctc)) > SECURITY_DEPOSIT_CTC_ABOVE


def _page_frame(canvas, _document):
    """A double green frame around the page, echoing the printed letterhead."""
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(GREEN)
    canvas.setLineWidth(2.2)
    canvas.roundRect(7 * mm, 7 * mm, width - 14 * mm, height - 14 * mm, 4 * mm)
    canvas.setLineWidth(0.7)
    canvas.roundRect(9 * mm, 9 * mm, width - 18 * mm, height - 18 * mm, 3 * mm)
    canvas.restoreState()


def offer_letter_pdf(offer) -> tuple[bytes, str]:
    """Render one offer as (pdf bytes, filename). Wording fixed with HR."""
    org = _org()
    application = offer.application
    candidate = application.candidate
    job = application.job_opening

    org_name = org.name if org else "Organisation"
    legal_name = (org.legal_name if org and org.legal_name else org_name)
    letter_date = timezone.localdate(offer.sent_at) if offer.sent_at else timezone.localdate()

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=15 * mm, bottomMargin=16 * mm,
        title=f"Offer Letter — {candidate.full_name}",
        author=legal_name,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "body", parent=styles["Normal"], fontSize=10, leading=15,
        textColor=INK, spaceAfter=8, alignment=4,  # justified
    )
    small = ParagraphStyle(
        "small", parent=body, fontSize=8.5, textColor=MUTED, spaceAfter=0, alignment=0
    )
    meta = ParagraphStyle("meta", parent=body, spaceAfter=2, alignment=0)
    title = ParagraphStyle(
        "title", parent=styles["Heading1"], fontSize=16, textColor=BRAND,
        spaceBefore=8, spaceAfter=10, alignment=1,
    )
    section = ParagraphStyle(
        "section", parent=styles["Heading2"], fontSize=11.5, textColor=BRAND,
        spaceBefore=12, spaceAfter=6,
    )
    clause_title = ParagraphStyle(
        "clause", parent=body, spaceBefore=2, spaceAfter=1, alignment=0
    )
    org_line = ParagraphStyle(
        "org", parent=styles["Heading1"], fontSize=15, textColor=BRAND, spaceAfter=1
    )

    story: list = []

    # ---- letterhead
    logo = _scaled_image(org.logo if org else None, max_width=60 * mm, max_height=19 * mm)
    head_right = [Paragraph(org_name, org_line)]
    if legal_name != org_name:
        head_right.append(Paragraph(legal_name, small))
    if logo is not None:
        story.append(Table(
            [[logo, head_right]],
            colWidths=[66 * mm, None],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (-1, -1), (-1, -1), 0),
            ]),
        ))
    else:
        story.extend(head_right)
    strip = _branch_strip()
    if strip:
        story.append(Paragraph(strip, ParagraphStyle(
            "strip", parent=small, alignment=2, textColor=BRAND, spaceBefore=2,
        )))
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=1.6, color=GREEN, spaceAfter=2))
    story.append(HRFlowable(width="100%", thickness=0.8, color=BRAND, spaceAfter=8))

    # ---- title + reference block (stacked, left-aligned, as HR specified)
    story.append(Paragraph("OFFER OF EMPLOYMENT", title))
    reference = f"OFR/{letter_date:%Y}/{str(offer.pk)[:8].upper()}"
    story.append(Paragraph(f"<b>Reference:</b> {reference}", meta))
    story.append(Paragraph(f"<b>Date:</b> {letter_date:%d %B %Y}", meta))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Dear {candidate.full_name},</b>", meta))
    story.append(Spacer(1, 6))

    designation = offer.designation.title if offer.designation_id else job.title
    department = job.department.name if job.department_id else ""
    location = job.location.name if job.location_id else ""

    story.append(Paragraph(
        f"We are pleased to offer you employment with <b>{legal_name}</b> for "
        f"the position of <b>{designation}</b>"
        + (f" in our <b>{department} Department</b>." if department else "."),
        body,
    ))
    story.append(Paragraph(
        "Following your application and the interview process, we were "
        "impressed by your qualifications, experience, and overall "
        "candidature. We look forward to welcoming you to our team and are "
        "confident that your contribution will add value to our organisation.",
        body,
    ))

    # ---- employment details
    story.append(Paragraph("Employment Details", section))
    rows = [["Particulars", "Details"], ["Position", designation]]
    if department:
        rows.append(["Department", department])
    if location:
        rows.append(["Work Location", location])
    if offer.level_id:
        rows.append(["Level", offer.level.name])
    if offer.reporting_manager_id:
        rows.append(["Reporting To", offer.reporting_manager.full_name])
    rows.append(["Date of Joining", f"{offer.joining_date:%d %B %Y}"])
    rows.append(["Annual CTC", f"{_inr(offer.offered_ctc)} per annum"])

    cell = ParagraphStyle("cell", parent=body, spaceAfter=0, alignment=0)
    head_cell = ParagraphStyle("hcell", parent=cell, textColor=BRAND)
    table_rows = [[
        Paragraph(f"<b>{rows[0][0]}</b>", head_cell),
        Paragraph(f"<b>{rows[0][1]}</b>", head_cell),
    ]] + [
        [Paragraph(f"<b>{label}</b>", cell), Paragraph(str(value), cell)]
        for label, value in rows[1:]
    ]
    story.append(Table(
        table_rows,
        colWidths=[52 * mm, None],
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, GREEN),
            ("BACKGROUND", (0, 0), (-1, 0), GREEN),
            ("BACKGROUND", (0, 1), (0, -1), GREEN_TINT),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]),
    ))

    # ---- terms & conditions (wording fixed with HR)
    story.append(Paragraph("Terms &amp; Conditions of Employment", section))
    clauses = [
        ("Background &amp; Document Verification",
         "Your employment is subject to the satisfactory verification of the "
         "documents, credentials, and information submitted by you during the "
         "<b>onboarding process through the organisation's HRMS platform</b>."),
        ("Terms of Employment",
         "The detailed terms and conditions of your employment, including "
         "working hours, leave entitlements, probation, responsibilities, and "
         "other applicable policies, will be governed by the organisation's HR "
         "policies and procedures. These terms will be formally communicated "
         "in your <b>Appointment Letter</b> upon joining."),
        ("Confidentiality",
         "You are required to maintain strict confidentiality regarding all "
         "information relating to the organisation, its operations, employees, "
         "patients, clients, and business affairs. This obligation shall "
         "remain applicable during and after your employment with the "
         "organisation."),
        ("Document Verification at Joining",
         "You are required to bring the original copies of your educational, "
         "identity, and other relevant documents on your date of joining for "
         "verification against the documents submitted through the HRMS "
         "onboarding process."),
        ("Organisational Policies",
         "Your employment will be subject to the rules, regulations, policies, "
         "procedures, and code of conduct applicable to employees of the "
         "organisation, as amended from time to time."),
    ]
    if includes_security_deposit(offer.offered_ctc):
        clauses.append((
            "Security Deposit Policy",
            "A security deposit equivalent to the employee's first month's "
            "salary will be collected as part of the employment terms. The "
            "deposit will be refunded upon successful completion of the agreed "
            "employment tenure, subject to the applicable terms and conditions "
            "of the organisation.",
        ))
    for index, (heading, text) in enumerate(clauses, start=1):
        story.append(Paragraph(f"<b>{index}. {heading}</b>", clause_title))
        story.append(Paragraph(text, body))

    # ---- acceptance
    story.append(Paragraph("Acceptance of Offer", section))
    story.append(Paragraph(
        f"We are delighted to offer you this opportunity and look forward to "
        f"having you as a valued member of <b>{legal_name}</b>.",
        body,
    ))
    acceptance = (
        "To accept this offer, please reply to the email through which this "
        "offer letter was sent, confirming your acceptance."
    )
    if offer.valid_until:
        acceptance += (
            f" This offer remains open for your acceptance until "
            f"<b>{offer.valid_until:%d %B %Y}</b>."
        )
    story.append(Paragraph(acceptance, body))
    story.append(Paragraph(
        "We wish you a successful and rewarding career with our organisation "
        "and look forward to welcoming you to the team.",
        body,
    ))

    # ---- signature block
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"<b>For {legal_name}</b>", meta))
    signature = _scaled_image(
        org.signature if org else None, max_width=38 * mm, max_height=17 * mm
    )
    if signature is not None:
        signature.hAlign = "LEFT"
        story.append(Spacer(1, 3))
        story.append(signature)
    else:
        story.append(Spacer(1, 20))
    if org and org.signatory_name:
        story.append(Paragraph(f"<b>{org.signatory_name}</b>", meta))
    if org and org.signatory_designation:
        story.append(Paragraph(f"<b>{org.signatory_designation}</b>", meta))

    document.build(story, onFirstPage=_page_frame, onLaterPages=_page_frame)
    filename = f"Offer Letter - {candidate.full_name}.pdf"
    return buffer.getvalue(), filename
