"""
Default onboarding configuration.

Everything here is DATA the organisation can change. It is seeded so a fresh
install has a working checklist and letter set, not because these particular
steps are special — the point of the template model is that the next
organisation deletes half of them and adds their own.

The default template is deliberately MINIMAL (policy, Aug 2026): the joiner
uploads only the MANDATORY documents and HR verifies each one; the handbook
is presented in-app right after the first successful password reset and the
employee acknowledges it there; and company email, system access and
departmental-head induction are handled outside onboarding entirely. Device
allocation is not a day-one requirement — it may follow within a month when
the role actually needs one.
"""

from __future__ import annotations

from django.db import transaction

from core.models import org_scoped

from .models import (
    ItemKind,
    ItemOwner,
    LetterTemplate,
    LetterType,
    OnboardingTemplate,
    OnboardingTemplateItem,
)

# --- documents the organisation collects -----------------------------------
# (code, name, category, mandatory, requires_expiry)
DOCUMENT_TYPES = [
    ("pan-card", "PAN card", "identity", True, False),
    ("aadhaar", "Aadhaar", "identity", True, False),
    ("passport", "Passport", "identity", False, True),
    ("address-proof", "Address proof", "address", False, False),
    ("degree-certificate", "Highest qualification certificate", "education", True, False),
    ("relieving-letter", "Experience / relieving letter", "experience", False, False),
    ("salary-slip", "Salary slips (last 3 months)", "experience", False, False),
    ("ctc-proof", "Current CTC proof", "experience", False, False),
    ("signed-agreement", "Signed employment agreement", "employment", True, False),
    ("bank-proof", "Bank account proof", "compliance", True, False),
    ("professional-registration", "Professional certificate", "compliance", False, True),
    ("other-supporting", "Other supporting documents", "other", False, False),
    ("photograph", "Passport photograph", "joining", False, False),
]

# (title, kind, owner, document code, mandatory, due offset in days, order)
#
# Ownership is deliberate: the EMPLOYEE uploads their own papers (HR verifies
# each one — verification, not upload, completes the line), and HR owns every
# administrative step. There is NO admin-owned onboarding work: the HR Head
# is the single owner of employee creation and onboarding.
DEFAULT_ITEMS = [
    # -- HR paperwork --
    ("Collect signed employment agreement", ItemKind.DOCUMENT, ItemOwner.HR, "signed-agreement", True, 0, 10),
    # -- the employee's mandatory documents (and ONLY the mandatory ones;
    #    anything optional is collected outside onboarding when it matters) --
    ("Upload PAN card", ItemKind.DOCUMENT, ItemOwner.EMPLOYEE, "pan-card", True, 2, 20),
    ("Upload Aadhaar card", ItemKind.DOCUMENT, ItemOwner.EMPLOYEE, "aadhaar", True, 2, 30),
    ("Upload highest qualification certificate", ItemKind.DOCUMENT, ItemOwner.EMPLOYEE, "degree-certificate", True, 7, 40),
    ("Collect bank account proof for payroll", ItemKind.DOCUMENT, ItemOwner.EMPLOYEE, "bank-proof", True, 3, 50),
    # Presented in-app immediately after the first successful password reset;
    # the employee acknowledges it there and that completes this line.
    ("Acknowledge the employee handbook and policies", ItemKind.ACKNOWLEDGEMENT, ItemOwner.EMPLOYEE, None, True, 5, 60),
    # -- setup that may follow later: not mandatory on day one, due a month
    #    out, and simply waived when the role needs no device --
    ("Allocate device and equipment (if required)", ItemKind.ASSET, ItemOwner.HR, None, False, 30, 150),
]

APPOINTMENT_BODY = """
<p>Dear {{ employee_name }},</p>
<p>We are pleased to confirm your appointment as <b>{{ designation }}</b> in the
{{ department }} department at {{ organization_name }}, effective
{{ date_of_joining }}.</p>
<p>You will be based at {{ location }} and will report to {{ manager }}. Your
employee code is {{ employee_code }}.</p>
<p>Your appointment is subject to the satisfactory completion of a probationary
period ending {{ probation_end_date }}. Confirmation of employment follows a
formal review and is communicated separately in writing.</p>
<p>We look forward to working with you.</p>
<p>{{ signatory_name }}<br/>{{ signatory_designation }}<br/>{{ organization_name }}</p>
"""

CONFIRMATION_BODY = """
<p>Dear {{ employee_name }},</p>
<p>Following the review of your probationary period, we are pleased to confirm
your employment with {{ organization_name }} as <b>{{ designation }}</b> in the
{{ department }} department, with effect from {{ confirmation_date }}.</p>
<p>Your performance during probation has been assessed and found satisfactory.
All terms of your appointment dated {{ date_of_joining }} continue to apply.</p>
<p>Congratulations, and thank you for your contribution.</p>
<p>{{ signatory_name }}<br/>{{ signatory_designation }}<br/>{{ organization_name }}</p>
"""

EXTENSION_BODY = """
<p>Dear {{ employee_name }},</p>
<p>Following the review of your probationary period, we are writing to inform
you that your probation has been extended.</p>
<p>The revised probation end date is {{ extended_to }}. Your reporting manager
will discuss the specific areas to focus on during this period and will review
your progress with you.</p>
<p>{{ signatory_name }}<br/>{{ signatory_designation }}<br/>{{ organization_name }}</p>
"""

JOINING_BODY = """
<p>Dear {{ employee_name }},</p>
<p>This letter confirms that you joined {{ organization_name }} on
{{ date_of_joining }} as <b>{{ designation }}</b> in the {{ department }}
department, under employee code {{ employee_code }}.</p>
<p>{{ signatory_name }}<br/>{{ signatory_designation }}<br/>{{ organization_name }}</p>
"""

EXPERIENCE_BODY = """
<p>To whom it may concern,</p>
<p>This is to certify that {{ employee_name }} (employee code
{{ employee_code }}) was employed with {{ organization_name }} as
<b>{{ designation }}</b> in the {{ department }} department from
{{ date_of_joining }}.</p>
<p>We wish them well in their future endeavours.</p>
<p>{{ signatory_name }}<br/>{{ signatory_designation }}<br/>{{ organization_name }}</p>
"""

LETTER_TEMPLATES = [
    (LetterType.APPOINTMENT, "Standard appointment letter",
     "Appointment as {{ designation }} - {{ organization_name }}", APPOINTMENT_BODY),
    (LetterType.JOINING, "Standard joining letter",
     "Confirmation of joining - {{ employee_name }}", JOINING_BODY),
    (LetterType.CONFIRMATION, "Standard confirmation letter",
     "Confirmation of employment - {{ employee_name }}", CONFIRMATION_BODY),
    (LetterType.EXTENSION, "Standard probation extension letter",
     "Extension of probation - {{ employee_name }}", EXTENSION_BODY),
    (LetterType.EXPERIENCE, "Standard experience letter",
     "Experience certificate - {{ employee_name }}", EXPERIENCE_BODY),
]

ASSET_CATEGORIES = [
    ("laptop", "Laptop", True, True),
    ("mobile", "Mobile phone", True, True),
    ("access-card", "Access card", True, True),
    ("headset", "Headset", False, True),
    ("furniture", "Furniture", False, True),
    ("stationery", "Stationery", False, False),
]


@transaction.atomic
def seed_document_types() -> int:
    from apps.employees.models import DocumentType

    for order, (code, name, category, mandatory, expiry) in enumerate(DOCUMENT_TYPES):
        org_scoped(DocumentType).update_or_create(
            code=code,
            defaults={
                "name": name,
                "category": category,
                "is_mandatory": mandatory,
                "requires_expiry": expiry,
                "order": order * 10,
            },
        )
    return len(DOCUMENT_TYPES)


@transaction.atomic
def seed_default_template() -> OnboardingTemplate:
    from apps.employees.models import DocumentType

    template, _ = org_scoped(OnboardingTemplate).update_or_create(
        name="Standard onboarding",
        defaults={
            "description": "Applies to any joiner without a more specific template.",
            "is_default": True,
        },
    )

    # Scoped: unscoped, a checklist item could point at ANOTHER
    # organization's document type -- a cross-tenant foreign key that
    # nothing downstream would catch.
    documents = {d.code: d for d in org_scoped(DocumentType)}
    for title, kind, owner, document_code, mandatory, offset, order in DEFAULT_ITEMS:
        org_scoped(OnboardingTemplateItem).update_or_create(
            template=template,
            order=order,
            defaults={
                "title": title,
                "kind": kind,
                "owner": owner,
                "document_type": documents.get(document_code) if document_code else None,
                "is_mandatory": mandatory,
                "due_offset_days": offset,
            },
        )
    # Steps dropped from the policy must LEAVE the template, or reseeding an
    # existing install would trim nothing. Only the default template is swept:
    # a template the organisation authored is theirs, not this seed's.
    template.items.exclude(order__in=[item[-1] for item in DEFAULT_ITEMS]).delete()
    return template


@transaction.atomic
def seed_letter_templates() -> int:
    for letter_type, name, subject, body in LETTER_TEMPLATES:
        org_scoped(LetterTemplate).update_or_create(
            letter_type=letter_type,
            name=name,
            defaults={"subject": subject, "body_html": body.strip(), "is_default": True},
        )
    return len(LETTER_TEMPLATES)


@transaction.atomic
def seed_asset_categories() -> int:
    from apps.assets.models import AssetCategory

    for code, name, requires_serial, returnable in ASSET_CATEGORIES:
        org_scoped(AssetCategory).update_or_create(
            code=code,
            defaults={
                "name": name,
                "requires_serial": requires_serial,
                "is_returnable": returnable,
            },
        )
    return len(ASSET_CATEGORIES)


@transaction.atomic
def seed_all() -> dict[str, int]:
    """Idempotent. Safe on every deploy."""
    counts = {
        "document_types": seed_document_types(),
        "letter_templates": seed_letter_templates(),
        "asset_categories": seed_asset_categories(),
    }
    template = seed_default_template()
    counts["onboarding_items"] = template.items.count()
    return counts
