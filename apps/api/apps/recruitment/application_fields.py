"""
The catalogue of questions an external application form may ask.

ONE definition, used by every path that presents or accepts an application:
the HRMS-hosted apply page, the Google Form built for a job, and the public
submission endpoint that validates what comes back. A Job Opening chooses
WHICH of these it asks (`JobOpening.application_fields`) and may add its own
free-form extras; it does not redefine the standard ones.

Where each answer lands:

  target="candidate"  → an existing Candidate column. Identity and the columns
                        the rest of recruitment already reads (experience,
                        current employer, expected pay).
  target="profile"    → Candidate.profile, a JSON document for the descriptive
                        attributes a form collects and HR reads on the profile
                        page but nothing else computes on. Adding twenty
                        nullable columns for these would give the schema
                        twenty more things to migrate every time a platform
                        adds a question, for no query that needs them.
  target="answers"    → Application.form_answers, for the job's own extras.
                        They belong to THIS application, not to the person.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str = "text"          # text | email | phone | number | date | url | select | textarea
    target: str = "profile"     # candidate | profile | answers
    required: bool = False
    options: tuple[str, ...] = ()
    help_text: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "target": self.target,
            "required": self.required,
            "options": list(self.options),
            "help_text": self.help_text,
        }


#: What a candidate is asked — and nothing more. Nine questions: who they
#: are, how to reach them, where they are, what they studied, their résumé
#: (uploaded or linked) and their pay. Everything HR needs downstream that is
#: not here (experience, employer, notice period) is filled in by HR on the
#: record, not demanded from the applicant; a long form loses applicants.
#:
#: `resume` is a FILE question: the hosted form uploads it and it is stored on
#: Candidate.resume. A Google Form cannot create upload questions through the
#: API, so there it is simply not asked and Resume Link carries the weight.
CATALOG: tuple[FieldSpec, ...] = (
    FieldSpec("full_name", "Full Name", "text", "candidate", required=True),
    FieldSpec("email", "Email", "email", "candidate", required=True),
    FieldSpec("phone", "Mobile Number", "phone", "candidate", required=True),
    FieldSpec("city", "City", "text"),
    FieldSpec("qualification", "Education", "text", help_text="Highest qualification, e.g. B.Sc. Nursing, MBA"),
    FieldSpec("resume", "Resume Upload", "file", "candidate", help_text="PDF or Word, up to 5 MB"),
    FieldSpec("resume_link", "Resume Link", "url", help_text="Google Drive, Dropbox or similar — make sure it is shared"),
    FieldSpec("current_salary", "Current Salary (per annum)", "number"),
    FieldSpec("expected_ctc", "Expected Salary (per annum)", "number", "candidate"),
)

BY_KEY: dict[str, FieldSpec] = {spec.key: spec for spec in CATALOG}

#: Asked on every form regardless of configuration. Without a name and a way
#: to reach the person there is nothing to create.
ALWAYS: tuple[str, ...] = ("full_name", "email", "phone")

_EXTRA_TYPES = frozenset({"text", "textarea", "number", "date", "url", "select"})
#: Question types the hosted form can collect. "file" is only meaningful there.
FILE_TYPE = "file"
#: What an uploaded résumé may be.
RESUME_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
RESUME_MAX_BYTES = 5 * 1024 * 1024


def fields_for(job) -> list[dict]:
    """
    The questions THIS job asks, in order, as plain dicts.

    `job.application_fields` is either empty (ask the whole catalogue) or a
    list whose entries are catalogue keys (strings) or extra questions (dicts
    with key/label/type/required/options). Unknown keys are ignored rather
    than refused, so removing a question from the catalogue never breaks a
    job that once asked it.
    """
    configured = list(job.application_fields or [])
    if not configured:
        return [spec.as_dict() for spec in CATALOG]

    seen: set[str] = set()
    out: list[dict] = []
    for key in ALWAYS:
        out.append(BY_KEY[key].as_dict())
        seen.add(key)
    for entry in configured:
        if isinstance(entry, str):
            spec = BY_KEY.get(entry)
            if spec and spec.key not in seen:
                out.append(spec.as_dict())
                seen.add(spec.key)
        elif isinstance(entry, dict) and entry.get("key") and entry["key"] not in seen:
            out.append(
                {
                    "key": str(entry["key"])[:60],
                    "label": str(entry.get("label") or entry["key"])[:160],
                    "type": entry.get("type") if entry.get("type") in _EXTRA_TYPES else "text",
                    "target": "answers",
                    "required": bool(entry.get("required", False)),
                    "options": [str(o)[:120] for o in (entry.get("options") or [])][:30],
                    "help_text": str(entry.get("help_text") or "")[:300],
                }
            )
            seen.add(entry["key"])
    return out


def validate_configuration(configured) -> list[dict]:
    """Serializer-side check of `application_fields`; returns cleaned entries."""
    if configured in (None, ""):
        return []
    if not isinstance(configured, list):
        raise ValueError("application_fields must be a list.")
    cleaned: list = []
    seen: set[str] = set()
    for entry in configured:
        if isinstance(entry, str):
            if entry not in BY_KEY:
                raise ValueError(f"'{entry}' is not a known application field.")
            key = entry
        elif isinstance(entry, dict):
            key = str(entry.get("key") or "").strip()
            if not key or len(key) > 60 or not key.replace("_", "").isalnum():
                raise ValueError("Each extra question needs a short alphanumeric key.")
            if key in BY_KEY:
                raise ValueError(f"'{key}' is a standard field; list it by name instead.")
            if entry.get("type") not in (None, *sorted(_EXTRA_TYPES)):
                raise ValueError(f"'{entry.get('type')}' is not a supported question type.")
            if entry.get("type") == "select" and not entry.get("options"):
                raise ValueError(f"'{key}' is a select question and needs options.")
        else:
            raise ValueError("application_fields entries must be field keys or question objects.")
        if key in seen:
            raise ValueError(f"'{key}' is listed twice.")
        seen.add(key)
        cleaned.append(entry)
    return cleaned
