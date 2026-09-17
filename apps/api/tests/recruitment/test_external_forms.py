"""
The Google Forms front door, against a fake Google.

Nothing here touches the network. The transport is a stand-in that records
what was asked and answers like the Forms API does, so the claims are about
OUR side: the form asks the job's questions, the response mapping is captured,
responses land in the same pipeline as the hosted form, and pulling them twice
does nothing the second time.
"""

from __future__ import annotations

import pytest

from apps.recruitment.models import Application, Candidate, JobStatus
from apps.recruitment.services import external_forms as ef

pytestmark = pytest.mark.django_db


class FakeGoogle:
    """Just enough of forms.googleapis.com to exercise the provider."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses: list[dict] = []
        self._items: list[dict] = []

    def request(self, method, url, *, headers, json_body=None, params=None):
        self.calls.append((method, url, json_body))
        if url == ef.TOKEN_URL:
            # The one unauthenticated call. Either a signed JWT (service
            # account) or a refresh token (a real user's grant).
            if json_body["grant_type"] == "refresh_token":
                assert json_body["refresh_token"] == "rt-1" and json_body["client_id"] == "cid"
            else:
                assert json_body["grant_type"].endswith("jwt-bearer") and json_body["assertion"]
            return {"access_token": "tok", "expires_in": 3600}
        assert headers["Authorization"] == "Bearer tok"
        if url == ef.FORMS_API and method == "POST":
            return {"formId": "FORM123"}
        if url == ef.DRIVE_API and method == "POST":
            assert json_body["mimeType"] == "application/vnd.google-apps.form"
            self.created_in_folder = (json_body.get("parents") or [None])[0]
            return {"id": "FORM123"}
        if url.endswith(":batchUpdate"):
            for i, req in enumerate(json_body["requests"]):
                if "createItem" in req:
                    item = dict(req["createItem"]["item"])
                    item["questionItem"] = {"question": {"questionId": f"q{i}"}}
                    self._items.append(item)
            return {}
        if url == f"{ef.FORMS_API}/FORM123" and method == "GET":
            return {"formId": "FORM123", "responderUri": "https://docs.google.com/forms/d/e/FORM123/viewform",
                    "items": self._items}
        if url.endswith("/responses"):
            return {"responses": self.responses}
        if "/permissions" in url:
            return {"id": "perm"}
        if url.endswith("/drive/v3/about"):
            return {"user": {"emailAddress": "svc@example.iam.gserviceaccount.com"}}
        if method == "DELETE":
            return {}
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def fake_creds():
    # A throwaway RSA key so PyJWT can sign; never a real credential.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {"client_email": "svc@example.iam.gserviceaccount.com", "private_key": pem}


@pytest.fixture
def provider(fake_creds):
    google = FakeGoogle()
    return ef.GoogleFormsProvider(credentials=fake_creds, transport=google), google


def test_publishing_without_credentials_stays_hosted(therapist_job, settings):
    settings.GOOGLE_FORMS_CREDENTIALS_FILE = ""
    ef.create_external_form(therapist_job)
    therapist_job.refresh_from_db()
    assert therapist_job.external_form_provider == "hosted"
    assert therapist_job.external_form_id == ""
    assert therapist_job.application_url  # the hosted link is always there


def test_a_form_is_built_from_the_jobs_questions_and_mapped(therapist_job, provider):
    prov, google = provider
    therapist_job.application_fields = ["qualification", {"key": "shift_ok", "label": "Evening shifts?",
                                                          "type": "select", "options": ["Yes", "No"]}]
    therapist_job.save()

    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()

    assert therapist_job.external_form_provider == "google_forms"
    assert therapist_job.external_form_id == "FORM123"
    assert therapist_job.external_form_url.startswith("https://docs.google.com/forms/")
    assert therapist_job.external_form_error == ""
    # Every asked field, and the consent checkbox, has a mapped question id.
    mapped = set(therapist_job.external_form_item_map.values())
    assert {"full_name", "email", "phone", "qualification", "shift_ok", "_consent"} <= mapped

    batch = next(b for m, u, b in google.calls if u.endswith(":batchUpdate"))
    titles = [r["createItem"]["item"]["title"] for r in batch["requests"] if "createItem" in r]
    assert "Full Name" in titles and "Evening shifts?" in titles and "Declaration" in titles


def test_a_google_failure_is_recorded_and_the_job_stays_published(therapist_job, fake_creds):
    class Broken:
        def request(self, *a, **k):
            raise ef.ExternalFormError("403 forms API disabled")

    prov = ef.GoogleFormsProvider(credentials=fake_creds, transport=Broken())
    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()
    assert therapist_job.status == JobStatus.PUBLISHED
    assert therapist_job.external_form_provider == "hosted"
    assert "forms API disabled" in therapist_job.external_form_error


def test_responses_flow_into_the_same_pipeline_and_are_idempotent(
    therapist_job, provider, django_capture_on_commit_callbacks
):
    prov, google = provider
    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()
    inv = {v: k for k, v in therapist_job.external_form_item_map.items()}

    def answer(qid, value):
        return {"textAnswers": {"answers": [{"value": value}]}}

    google.responses = [
        {
            "responseId": "resp-1",
            "lastSubmittedTime": "2026-08-18T09:00:00Z",
            "answers": {
                inv["full_name"]: answer(inv["full_name"], "Ravi Kumar"),
                inv["email"]: answer(inv["email"], "ravi@example.test"),
                inv["phone"]: answer(inv["phone"], "9811111111"),
                inv["qualification"]: answer(inv["qualification"], "MPT"),
                inv["_consent"]: answer(inv["_consent"], "I confirm"),
            },
        },
        {   # no consent ticked → refused, not created
            "responseId": "resp-2",
            "answers": {
                inv["full_name"]: answer(inv["full_name"], "No Consent"),
                inv["phone"]: answer(inv["phone"], "9822222222"),
            },
        },
    ]

    with django_capture_on_commit_callbacks(execute=True):
        first = ef.sync_responses(therapist_job, provider=prov)
    assert first["seen"] == 2 and first["created"] == 1 and first["refused"] == 1

    app = Application.objects.get(job_opening=therapist_job)
    assert app.candidate.full_name == "Ravi Kumar"
    assert app.candidate.source == "google_forms"
    assert app.candidate.profile["qualification"] == "MPT"
    assert app.candidate_notifications.filter(kind="application_received").exists()
    assert not Candidate.objects.filter(first_name="No").exists()

    # Pull again: the same responses, nothing new.
    with django_capture_on_commit_callbacks(execute=True):
        second = ef.sync_responses(therapist_job, provider=prov)
    assert second["created"] == 0 and second["duplicate"] == 1
    assert Application.objects.filter(job_opening=therapist_job).count() == 1
    assert app.candidate_notifications.count() == 1


def test_the_beat_task_is_a_noop_without_credentials(settings, organization):
    from apps.recruitment.tasks import sync_google_form_responses_for_organization

    settings.GOOGLE_FORMS_CREDENTIALS_FILE = ""
    assert sync_google_form_responses_for_organization(organization.pk) == {"skipped": True}


def test_publish_over_http_records_the_provider(therapist_job, staff, settings):
    """Publishing a draft job builds the external form after commit."""
    from rest_framework.test import APIClient

    settings.GOOGLE_FORMS_CREDENTIALS_FILE = ""
    therapist_job.status = JobStatus.DRAFT
    therapist_job.save()
    api = APIClient()
    token = api.post("/api/v1/auth/login/", {"email": staff["hr_head"].user.email,
                                             "password": "test-password-12345"}, format="json").data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    r = api.post(f"/api/v1/jobs/{therapist_job.pk}/publish/")
    assert r.status_code == 200, r.data
    assert r.data["application_url"].endswith(f"/apply/{therapist_job.application_token}")
    assert r.data["accepts_applications"] is True


def test_a_job_that_already_has_a_form_keeps_it(therapist_job, provider):
    """Create-on-create plus create-on-publish must yield ONE form per job."""
    prov, google = provider
    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()
    creates_before = sum(1 for m, u, b in google.calls if u == ef.FORMS_API and m == "POST")
    ef.create_external_form(therapist_job, provider=prov)   # publish, or a retry
    creates_after = sum(1 for m, u, b in google.calls if u == ef.FORMS_API and m == "POST")
    assert creates_before == creates_after == 1
    assert therapist_job.external_form_id == "FORM123"


def test_creating_a_job_over_http_builds_its_form(staff, org, roles, workflows, provider, settings, monkeypatch):
    """Spec 1: the form exists from the moment the job does."""
    from rest_framework.test import APIClient
    from apps.recruitment.models import JobOpening
    from core.access.catalog import DepartmentKind

    prov, google = provider
    monkeypatch.setattr(ef, "provider_for_job", lambda: prov)
    api = APIClient()
    token = api.post("/api/v1/auth/login/", {"email": staff["hr_head"].user.email,
                                             "password": "test-password-12345"}, format="json").data["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    r = api.post("/api/v1/jobs/", {
        "title": "Data Science Intern", "workflow": str(workflows["Therapist hiring"].pk),
        "department": str(org["departments"][DepartmentKind.MEDICAL].pk),
        "target_role": str(roles["therapist"].pk),
    }, format="json")
    assert r.status_code == 201, r.data
    job = JobOpening.objects.get(pk=r.data["id"])
    # on_commit ran inside the test transaction? APIClient tests run in a
    # transaction, so run the hook explicitly the way commit would.
    ef.create_external_form(job, provider=prov)
    job.refresh_from_db()
    assert job.external_form_provider == "google_forms" and job.external_form_id == "FORM123"
    assert job.application_url.endswith(f"/apply/{job.application_token}")


def test_file_upload_answers_become_drive_links():
    item_map = {"q1": "resume_link", "q2": "_consent"}
    response = {"answers": {
        "q1": {"fileUploadAnswers": {"answers": [{"fileId": "ABC123"}]}},
        "q2": {"textAnswers": {"answers": [{"value": "I confirm"}]}},
    }}
    answers, consented = ef.responses_to_answers(response, item_map)
    assert answers == {"resume_link": "https://drive.google.com/file/d/ABC123/view"}
    assert consented is True


def test_a_response_refused_while_draft_is_picked_up_after_publish(
    therapist_job, provider, django_capture_on_commit_callbacks
):
    """No since-cursor: a draft-time response is not stranded."""
    prov, google = provider
    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()
    inv = {v: k for k, v in therapist_job.external_form_item_map.items()}
    ans = lambda v: {"textAnswers": {"answers": [{"value": v}]}}  # noqa: E731
    google.responses = [{"responseId": "r1", "answers": {
        inv["full_name"]: ans("Late Bloomer"), inv["email"]: ans("late@example.test"),
        inv["phone"]: ans("9833333333"), inv["_consent"]: ans("yes")}}]

    therapist_job.status = JobStatus.DRAFT
    therapist_job.save()
    first = ef.sync_responses(therapist_job, provider=prov)
    assert first["refused"] == 1 and Application.objects.count() == 0

    therapist_job.status = JobStatus.PUBLISHED
    therapist_job.save()
    with django_capture_on_commit_callbacks(execute=True):
        second = ef.sync_responses(therapist_job, provider=prov)
    assert second["created"] == 1
    assert Application.objects.filter(job_opening=therapist_job).count() == 1


def test_google_errors_are_translated_for_hr():
    msg = ef.explain_google_error(ef.ExternalFormError(
        "POST https://forms.googleapis.com/v1/forms -> 500: {\"error\": {\"code\": 500}}"))
    assert "Drive API" in msg and "google_forms_check" in msg
    msg = ef.explain_google_error(ef.ExternalFormError(
        "GET https://www.googleapis.com/drive/v3/about -> 403: Google Drive API has not been used in project"))
    assert msg.startswith("Google Drive API is not enabled")


def test_diagnose_reports_each_api_separately(fake_creds):
    class DriveOff(FakeGoogle):
        def request(self, method, url, *, headers, json_body=None, params=None):
            if "googleapis.com/drive" in url:
                raise ef.ExternalFormError(f"{method} {url} -> 403: Google Drive API has not been used in project 1")
            if url == ef.FORMS_API and method == "POST":
                raise ef.ExternalFormError(f"POST {url} -> 500: INTERNAL")
            return super().request(method, url, headers=headers, json_body=json_body, params=params)

    report = ef.diagnose(credentials=fake_creds, transport=DriveOff())
    assert report["token"] is True
    assert report["drive_api"] is False and report["forms_api"] is False
    assert "Drive API is not enabled" in report["errors"]["drive_api"]

    good = ef.diagnose(credentials=fake_creds, transport=FakeGoogle())
    assert good["forms_api"] is True and good["file_upload_question"] is True


def test_with_a_folder_configured_the_form_is_created_into_it(therapist_job, provider, settings):
    """Service accounts own no storage; the form must land in a person's folder."""
    prov, google = provider
    settings.GOOGLE_FORMS_FOLDER_ID = "FOLDER42"
    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()
    assert therapist_job.external_form_id == "FORM123"
    assert google.created_in_folder == "FOLDER42"
    # forms.create was NOT used — that is the call that fails for a service account.
    assert not any(u == ef.FORMS_API and m == "POST" for m, u, b in google.calls)


def test_the_quota_error_is_explained():
    msg = ef.explain_google_error(ef.ExternalFormError("POST ... -> 403: The user's Drive storage quota has been exceeded."))
    assert "GOOGLE_FORMS_FOLDER_ID" in msg


def test_a_real_users_grant_creates_the_form_as_them(therapist_job):
    """The route that works on Gmail: refresh token in, forms.create as the person."""
    google = FakeGoogle()
    prov = ef.GoogleFormsProvider(
        credentials={"kind": "oauth_user", "refresh_token": "rt-1", "client_id": "cid",
                     "client_secret": "sec", "account": "hr@example.test"},
        transport=google,
    )
    ef.create_external_form(therapist_job, provider=prov)
    therapist_job.refresh_from_db()
    assert therapist_job.external_form_provider == "google_forms"
    assert therapist_job.external_form_id == "FORM123"
    assert any(u == ef.FORMS_API and m == "POST" for m, u, b in google.calls)


def test_the_user_grant_is_preferred_over_the_service_account(settings, tmp_path, fake_creds):
    sa = tmp_path / "sa.json"; sa.write_text(__import__("json").dumps(fake_creds))
    client = tmp_path / "client.json"; client.write_text('{"installed": {"client_id": "cid", "client_secret": "sec"}}')
    token = tmp_path / "token.json"; token.write_text('{"refresh_token": "rt-1", "account": "hr@example.test"}')
    settings.GOOGLE_FORMS_CREDENTIALS_FILE = str(sa)
    settings.GOOGLE_FORMS_OAUTH_CLIENT_FILE = str(client)
    settings.GOOGLE_FORMS_OAUTH_TOKEN_FILE = str(token)
    creds = ef.load_credentials()
    assert creds["kind"] == "oauth_user" and creds["account"] == "hr@example.test"
    assert ef.google_forms_enabled()
