"""
External application forms for a job opening.

A Job Opening is the source of truth. An external form is a second front door
that leads to the same intake as the HRMS-hosted page — it collects the same
questions (`application_fields.fields_for(job)`) and its responses go through
`public_intake.public_apply()` under the source "google_forms", so identity,
consent, idempotency and the "application received" email behave identically.

PROVIDERS
---------
  hosted        The HRMS's own /apply/<token> page. Always available, needs no
                configuration, and is what the "Copy Application Link" button
                copies. Every job has it.
  google_forms  A Google Form created through the Forms API when a job is
                published, with responses pulled by a scheduled task. ENABLED
                ONLY when a service-account credential file is configured
                (`GOOGLE_FORMS_CREDENTIALS_FILE`); without one, publishing a
                job records "hosted" and moves on. Nothing about hiring waits
                on Google.

TWO KINDS OF CREDENTIAL, AND WHY THE SECOND EXISTS
--------------------------------------------------
There is no user at the keyboard when a job is published, so the natural
credential is a service account (`GOOGLE_FORMS_CREDENTIALS_FILE`). Learned the
hard way against a real project: Google gives service accounts NO Drive
storage, and a Google Form is charged to its creator, so a service account
cannot create a form at all outside a Workspace Shared Drive — `forms.create`
answers a bare 500. On a Gmail-based organisation that is a dead end.

So the integration also accepts a REAL user's OAuth grant
(`GOOGLE_FORMS_OAUTH_CLIENT_FILE` + `GOOGLE_FORMS_OAUTH_TOKEN_FILE`): HR
authorises once with `manage.py google_forms_authorize`, the refresh token is
kept as a secret file, and every form is created as that person — owned by
them, in their own Drive, editable by them without any sharing. When both are
configured the user grant wins, because it is the one that works everywhere.

The HTTP transport is injectable so the whole thing is unit-tested against a
fake, and so an operator can point it at a proxy. Live credentials are never
read by tests.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.utils import timezone

from apps.recruitment.application_fields import fields_for

logger = logging.getLogger("hrms.recruitment.forms")

FORMS_API = "https://forms.googleapis.com/v1/forms"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/drive.file",
    # Interview events + Meet links — the Calendar integration shares this
    # grant. A token recorded before this scope existed still works for
    # Forms; Calendar calls fail with "insufficient scopes" until
    # `google_forms_authorize` is re-run once.
    "https://www.googleapis.com/auth/calendar.events",
)


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: dict, json_body=None, params=None) -> dict: ...


class RequestsTransport:
    """The real thing. `requests` is already in the lock file."""

    def request(self, method, url, *, headers, json_body=None, params=None) -> dict:
        import requests

        response = requests.request(
            method, url, headers=headers, json=json_body, params=params, timeout=20
        )
        if response.status_code >= 400:
            raise ExternalFormError(
                f"{method} {url} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else {}


class ExternalFormError(RuntimeError):
    """A provider call that did not succeed. Recorded on the job, never raised to HR."""


@dataclass
class FormHandle:
    provider: str
    form_id: str
    url: str


# ---------------------------------------------------------------- credentials


def _load_service_account() -> dict | None:
    path = getattr(settings, "GOOGLE_FORMS_CREDENTIALS_FILE", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("recruitment.google_forms_credentials_unreadable %s", exc)
        return None


def _load_oauth_token() -> dict | None:
    """The refresh-token file written by `google_forms_authorize`, plus the client."""
    token_path = getattr(settings, "GOOGLE_FORMS_OAUTH_TOKEN_FILE", "")
    client_path = getattr(settings, "GOOGLE_FORMS_OAUTH_CLIENT_FILE", "")
    if not (token_path and client_path):
        return None
    try:
        with open(token_path, encoding="utf-8") as fh:
            token = json.load(fh)
        with open(client_path, encoding="utf-8") as fh:
            client = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error("recruitment.google_forms_oauth_unreadable %s", exc)
        return None
    client = client.get("installed") or client.get("web") or client
    if not token.get("refresh_token") or not client.get("client_id"):
        return None
    return {"kind": "oauth_user", "refresh_token": token["refresh_token"],
            "client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
            "account": token.get("account", "")}


def google_forms_enabled() -> bool:
    return bool(
        getattr(settings, "GOOGLE_FORMS_OAUTH_TOKEN_FILE", "")
        or getattr(settings, "GOOGLE_FORMS_CREDENTIALS_FILE", "")
    )


class UserToken:
    """
    An access token from a stored refresh token — the one-time OAuth grant a
    real person gave via `google_forms_authorize`. Same shape as
    ServiceAccountToken so the provider does not care which it holds.
    """

    def __init__(self, credentials: dict, transport: Transport):
        self._creds = credentials
        self._transport = transport
        self._token = ""
        self._expires_at = 0.0

    def get(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        data = self._transport.request(
            "POST", TOKEN_URL, headers={"Content-Type": "application/json"},
            json_body={
                "grant_type": "refresh_token",
                "refresh_token": self._creds["refresh_token"],
                "client_id": self._creds["client_id"],
                "client_secret": self._creds["client_secret"],
            },
        )
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._token


def token_for(credentials: dict, transport: Transport):
    if credentials.get("kind") == "oauth_user":
        return UserToken(credentials, transport)
    return ServiceAccountToken(credentials, transport)


class ServiceAccountToken:
    """
    An OAuth2 access token minted from a service-account key: sign a JWT with
    the key, exchange it. Cached until a minute before expiry. Uses PyJWT and
    cryptography, both already dependencies.
    """

    def __init__(self, credentials: dict, transport: Transport):
        self._creds = credentials
        self._transport = transport
        self._token = ""
        self._expires_at = 0.0

    def get(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        import jwt

        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._creds["client_email"],
                "scope": " ".join(SCOPES),
                "aud": TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            self._creds["private_key"],
            algorithm="RS256",
        )
        data = self._transport.request(
            "POST",
            TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json_body={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._token


# ---------------------------------------------------------------- the provider


class GoogleFormsProvider:
    def __init__(self, *, credentials: dict, transport: Transport | None = None):
        self._transport = transport or RequestsTransport()
        self._token = token_for(credentials, self._transport)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token.get()}", "Content-Type": "application/json"}

    # ---- create -------------------------------------------------------

    def create_form(self, job) -> FormHandle:
        """
        One form per job: title from the job, one question per configured
        field, plus the consent declaration as a required checkbox. Item ids
        are what the response payload is keyed on, so the mapping from item
        to field key is stored on the job for the sync to read.
        """
        form_id = self._create_blank_form(f"{job.title} — Application")

        requests_ = [
            {"updateFormInfo": {
                "info": {"description": (job.description or "")[:4000]},
                "updateMask": "description",
            }}
        ]
        # The Forms API cannot create upload questions, so a "file" field is
        # simply not asked there — Resume Link carries that weight.
        asked = [spec for spec in fields_for(job) if spec["type"] != "file"]
        for index, spec in enumerate(asked):
            requests_.append({"createItem": {"item": _item_for(spec), "location": {"index": index}}})
        from apps.recruitment.services.public_intake import CONSENT_TEXT

        requests_.append({"createItem": {
            "item": {
                "title": "Declaration",
                "questionItem": {"question": {"required": True, "choiceQuestion": {
                    "type": "CHECKBOX", "options": [{"value": CONSENT_TEXT}]}}},
            },
            "location": {"index": len(requests_) - 1},
        }})
        self._transport.request(
            "POST", f"{FORMS_API}/{form_id}:batchUpdate", headers=self._headers(),
            json_body={"requests": requests_},
        )

        # Read the form back for item ids → the mapping the sync needs.
        form = self._transport.request("GET", f"{FORMS_API}/{form_id}", headers=self._headers())
        mapping = {}
        keys = [spec["key"] for spec in fields_for(job) if spec["type"] != "file"] + ["_consent"]
        for item, key in zip(form.get("items", []), keys):
            question_id = (item.get("questionItem", {}).get("question", {}) or {}).get("questionId")
            if question_id:
                mapping[question_id] = key
        job.application_fields_item_map = mapping  # transient; persisted by caller

        share_with = getattr(settings, "GOOGLE_FORMS_SHARE_WITH", "")
        if share_with:
            try:
                self._transport.request(
                    "POST", f"{DRIVE_API}/{form_id}/permissions", headers=self._headers(),
                    json_body={"type": "user", "role": "writer", "emailAddress": share_with},
                    params={"sendNotificationEmail": "false"},
                )
            except ExternalFormError as exc:
                logger.warning("recruitment.google_forms_share_failed form=%s %s", form_id, exc)

        return FormHandle("google_forms", form_id, form.get("responderUri", ""))

    def _create_blank_form(self, title: str) -> str:
        """
        A form the service account can actually make.

        Google gives service accounts NO Drive storage, so `forms.create` — which
        would put the form in the account's own Drive — fails with a bare 500.
        The form has to be created INTO a folder a real person owns and has
        shared with the account (`GOOGLE_FORMS_FOLDER_ID`); Drive then charges
        the storage to that person and the Forms API edits it as usual. Without
        a folder configured we still try `forms.create`, so an environment where
        the account does have storage (Workspace, shared drives) keeps working.
        """
        folder = getattr(settings, "GOOGLE_FORMS_FOLDER_ID", "")
        if folder:
            created = self._transport.request(
                "POST", DRIVE_API, headers=self._headers(),
                json_body={"name": title, "mimeType": "application/vnd.google-apps.form",
                           "parents": [folder]},
                params={"supportsAllDrives": "true", "fields": "id"},
            )
            form_id = created["id"]
            self._transport.request(
                "POST", f"{FORMS_API}/{form_id}:batchUpdate", headers=self._headers(),
                json_body={"requests": [{"updateFormInfo": {"info": {"title": title}, "updateMask": "title"}}]},
            )
            return form_id
        created = self._transport.request(
            "POST", FORMS_API, headers=self._headers(),
            json_body={"info": {"title": title, "documentTitle": title}},
        )
        return created["formId"]

    # ---- responses ----------------------------------------------------

    def list_responses(self, form_id: str, *, since=None) -> list[dict]:
        params = {}
        if since is not None:
            params["filter"] = f"timestamp >= {since.isoformat()}"
        rows: list[dict] = []
        token = None
        while True:
            if token:
                params["pageToken"] = token
            page = self._transport.request(
                "GET", f"{FORMS_API}/{form_id}/responses", headers=self._headers(), params=params
            )
            rows.extend(page.get("responses", []))
            token = page.get("nextPageToken")
            if not token:
                return rows


def _item_for(spec: dict) -> dict:
    """A Forms API item for one catalogue field."""
    kind = spec["type"]
    question: dict = {"required": bool(spec["required"])}
    if kind == "select":
        question["choiceQuestion"] = {
            "type": "DROP_DOWN", "options": [{"value": o} for o in spec["options"]]
        }
    elif kind == "date":
        question["dateQuestion"] = {"includeYear": True}
    elif kind == "textarea":
        question["textQuestion"] = {"paragraph": True}
    else:
        question["textQuestion"] = {"paragraph": False}
    item = {"title": spec["label"], "questionItem": {"question": question}}
    if spec.get("help_text"):
        item["description"] = spec["help_text"]
    return item


def responses_to_answers(response: dict, item_map: dict) -> tuple[dict, bool]:
    """
    (answers keyed by field key, consented) from one Forms API response.

    A file-upload answer (a question HR added to the form by hand — the API
    cannot create one) arrives as Drive file ids; they become Drive links so
    the profile can open them. Whichever field the upload was mapped to
    receives the link(s), newline-separated.
    """
    answers: dict = {}
    consented = False
    for question_id, block in (response.get("answers") or {}).items():
        key = item_map.get(question_id)
        if not key:
            continue
        values = [a.get("value", "") for a in (block.get("textAnswers", {}) or {}).get("answers", [])]
        files = (block.get("fileUploadAnswers", {}) or {}).get("answers", [])
        if files:
            values = [f"https://drive.google.com/file/d/{f.get('fileId')}/view" for f in files if f.get("fileId")]
        if key == "_consent":
            consented = bool(values)
            continue
        answers[key] = values[0] if len(values) == 1 else "\n".join(values)
    return answers, consented


def explain_google_error(exc: Exception) -> str:
    """
    Turn Google's failure into the sentence HR needs. The Forms API answers
    500 INTERNAL when the Drive API is disabled on the project — a form IS a
    Drive file — which is opaque enough to be worth translating.
    """
    text = str(exc)
    if "Drive API has not been used" in text or "drive.googleapis.com" in text:
        return ("Google Drive API is not enabled on the Google Cloud project of the "
                "service account. Enable it (APIs & Services > Library > Google Drive API), "
                "then re-run `manage.py google_forms_check`. Detail: " + text)
    if "storage quota has been exceeded" in text:
        return ("The service account has no Drive storage of its own (Google gives "
                "service accounts none), so it cannot own a form. Create a folder in a "
                "real Google account's Drive, share it with the service account as Editor, "
                "and set GOOGLE_FORMS_FOLDER_ID to that folder's id. Detail: " + text)
    if "forms.googleapis.com/v1/forms -> 500" in text:
        return ("Google Forms API returned 500 INTERNAL on create. This is what it "
                "does when the Google Drive API is disabled on the project, or when the "
                "service account has no Drive storage to own the form (it never does): "
                "set GOOGLE_FORMS_FOLDER_ID to a folder a real account owns and has shared "
                "with it as Editor. Re-run `manage.py google_forms_check`. Detail: " + text)
    if "-> 403" in text and "forms.googleapis.com" in text:
        return "Google Forms API is not enabled on the project, or the key lacks the scope. Detail: " + text
    return f"{type(exc).__name__}: {text}"


def diagnose(*, credentials: dict | None = None, transport: Transport | None = None) -> dict:
    """
    What `manage.py google_forms_check` runs: token, Forms API, Drive API,
    file-upload support, share target — each reported separately, nothing
    left behind (the probe form is deleted).
    """
    out: dict = {"credentials": False, "token": False, "forms_api": False, "drive_api": False,
                 "file_upload_question": False, "share_with": getattr(settings, "GOOGLE_FORMS_SHARE_WITH", ""),
                 "errors": {}}
    creds = credentials or load_credentials()
    if not creds:
        out["errors"]["credentials"] = (
            "No Google credential: set GOOGLE_FORMS_OAUTH_CLIENT_FILE + GOOGLE_FORMS_OAUTH_TOKEN_FILE "
            "(run `manage.py google_forms_authorize`), or GOOGLE_FORMS_CREDENTIALS_FILE.")
        return out
    out["credentials"] = True
    out["kind"] = creds.get("kind", "service_account")
    out["client_email"] = creds.get("account") or creds.get("client_email", "")
    transport = transport or RequestsTransport()
    try:
        token = token_for(creds, transport).get()
        out["token"] = True
    except Exception as exc:  # noqa: BLE001
        out["errors"]["token"] = str(exc)[:300]
        return out
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        transport.request("GET", "https://www.googleapis.com/drive/v3/about", headers=headers,
                          params={"fields": "user"})
        out["drive_api"] = True
    except Exception as exc:  # noqa: BLE001
        out["errors"]["drive_api"] = explain_google_error(exc)[:400]
    out["folder_id"] = getattr(settings, "GOOGLE_FORMS_FOLDER_ID", "")
    form_id = None
    try:
        provider = GoogleFormsProvider(credentials=creds, transport=transport)
        form_id = provider._create_blank_form("HRMS connectivity check")
        out["forms_api"] = True
    except Exception as exc:  # noqa: BLE001
        out["errors"]["forms_api"] = explain_google_error(exc)[:400]
    if form_id:
        try:
            transport.request("POST", f"{FORMS_API}/{form_id}:batchUpdate", headers=headers, json_body={
                "requests": [{"createItem": {"item": {"title": "Resume", "questionItem": {"question": {
                    "fileUploadQuestion": {"folderId": "root", "maxFiles": 1}}}}, "location": {"index": 0}}}]})
            out["file_upload_question"] = True
        except Exception as exc:  # noqa: BLE001
            out["errors"]["file_upload_question"] = str(exc)[:200]
        try:
            transport.request("DELETE", f"{DRIVE_API}/{form_id}", headers=headers)
        except Exception as exc:  # noqa: BLE001
            out["errors"]["cleanup"] = f"probe form {form_id} left behind: {str(exc)[:120]}"
    return out


# ---------------------------------------------------------------- orchestration


def load_credentials() -> dict | None:
    """The user grant if there is one; else the service account; else nothing."""
    return _load_oauth_token() or _load_service_account()


def provider_for_job() -> GoogleFormsProvider | None:
    creds = load_credentials()
    if creds is None:
        return None
    return GoogleFormsProvider(credentials=creds)


def create_external_form(job, *, provider: GoogleFormsProvider | None = None) -> None:
    """
    Called when a job is created and again when it is published. Never
    raises: a Google outage must not stop either, and the hosted link exists
    regardless. Idempotent — a job that already has a form keeps it, so the
    two calls (and any retry) produce ONE form per job, which is what keeps
    Job A's applicants out of Job B.
    """
    if job.external_form_provider == "google_forms" and job.external_form_id:
        return
    provider = provider or provider_for_job()
    if provider is None:
        job.external_form_provider = "hosted"
        job.save(update_fields=["external_form_provider", "updated_at"])
        return
    try:
        handle = provider.create_form(job)
    except Exception as exc:  # noqa: BLE001 — recorded on the job for HR to see
        logger.exception("recruitment.google_forms_create_failed job=%s", job.pk)
        job.external_form_provider = "hosted"
        job.external_form_error = explain_google_error(exc)[:2000]
        job.save(update_fields=["external_form_provider", "external_form_error", "updated_at"])
        return

    job.external_form_provider = handle.provider
    job.external_form_id = handle.form_id
    job.external_form_url = handle.url
    job.external_form_error = ""
    # The item→field mapping rides along in application_fields' sibling
    # column so the sync can decode responses without a second model.
    job.external_form_item_map = getattr(job, "application_fields_item_map", {})
    job.save(update_fields=[
        "external_form_provider", "external_form_id", "external_form_url",
        "external_form_error", "external_form_item_map", "updated_at",
    ])


def sync_responses(job, *, provider: GoogleFormsProvider | None = None) -> dict:
    """
    Pull new responses for one job's Google Form into the pipeline.

    Every response goes through `public_apply` under source "google_forms" with
    the response's own id as the submission id — so re-pulling the same
    responses is a no-op, exactly as re-POSTing the hosted form is.
    """
    from apps.recruitment.services.public_intake import PublicIntakeError, public_apply

    if job.external_form_provider != "google_forms" or not job.external_form_id:
        return {"skipped": True}
    provider = provider or provider_for_job()
    if provider is None:
        return {"skipped": True, "reason": "no credentials"}

    counts = {"seen": 0, "created": 0, "duplicate": 0, "refused": 0}
    try:
        # The WHOLE response set every time, not a since-cursor. Every response
        # is keyed by its own id through public_apply(), so re-reading is a
        # no-op — and a cursor would silently strand a response that arrived
        # while the job was still a draft and was refused on first sight.
        responses = provider.list_responses(job.external_form_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("recruitment.google_forms_sync_failed job=%s", job.pk)
        job.external_form_error = f"{type(exc).__name__}: {exc}"[:2000]
        job.save(update_fields=["external_form_error", "updated_at"])
        return {**counts, "error": str(exc)}

    for response in responses:
        counts["seen"] += 1
        answers, consented = responses_to_answers(response, job.external_form_item_map or {})
        try:
            result = public_apply(
                job=job,
                submission_id=response.get("responseId", ""),
                answers=answers,
                consented=consented,
                source="google_forms",
                submitted_at=_parse_ts(response.get("lastSubmittedTime")),
            )
        except PublicIntakeError as exc:
            counts["refused"] += 1
            logger.warning(
                "recruitment.google_forms_response_refused job=%s response=%s %s",
                job.pk, response.get("responseId"), exc.message_dict if hasattr(exc, "message_dict") else exc,
            )
            continue
        counts["created" if result.created_application else "duplicate"] += 1

    job.external_form_synced_at = timezone.now()
    job.external_form_error = ""
    job.save(update_fields=["external_form_synced_at", "external_form_error", "updated_at"])
    return counts


def _parse_ts(value):
    from django.utils.dateparse import parse_datetime

    return parse_datetime(value) if value else None
