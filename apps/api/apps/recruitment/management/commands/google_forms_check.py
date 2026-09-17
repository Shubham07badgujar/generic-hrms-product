"""
Is Google Forms integration ready? One command, plain answers.

    manage.py google_forms_check
    manage.py google_forms_check --create-for <job id>   # build the form for one job now

Reads GOOGLE_FORMS_CREDENTIALS_FILE, mints a token, and tries each API the
integration needs. Prints what works and, for what does not, the sentence an
administrator needs to fix it. Leaves nothing behind in the Google account.

TWO HALVES, TWO SCOPES. The credential check is about the DEPLOYMENT: the
Google service account is not per-organization yet, so asking which company
to check it for would be a question with no meaningful answer, and it stays
runnable with no organization named. `--create-for` acts on one job opening,
which belongs to one organization, so that half resolves one by the same
refuse-to-guess rule as every `OrganizationCommand` and looks the job up only
inside it. A job id from another company is reported as not found, the same
answer as an id that does not exist.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.management.orgcommand import resolve_organization
from core.middleware import acting_as


class Command(BaseCommand):
    help = "Check Google Forms credentials and API access; optionally build a job's form."

    def add_arguments(self, parser):
        parser.add_argument("--create-for", metavar="JOB_ID", help="Create the Google Form for this job now.")
        parser.add_argument(
            "--organization",
            metavar="SLUG",
            help=(
                "Organization that owns the --create-for job. Optional while a "
                "deployment has exactly one; ignored by the credential check."
            ),
        )

    def handle(self, *args, **options):
        from apps.recruitment.services import external_forms as ef

        report = ef.diagnose()
        ok = lambda flag: self.style.SUCCESS("OK   ") if flag else self.style.ERROR("FAIL ")  # noqa: E731
        self.stdout.write(
            f"{ok(report['credentials'])} credential        "
            f"{report.get('kind', '')} {report.get('client_email', '')}".rstrip()
        )
        self.stdout.write(f"{ok(report['token'])} OAuth token")
        self.stdout.write(f"{ok(report['drive_api'])} Google Drive API")
        self.stdout.write(f"{ok(report['forms_api'])} Google Forms API (create)")
        self.stdout.write(
            f"{'OK   ' if report['file_upload_question'] else 'n/a  '} file-upload question via API "
            f"({'supported' if report['file_upload_question'] else 'not creatable via API - use a Drive-link field, or add by hand in the form'})"
        )
        self.stdout.write(f"       forms folder:     {report.get('folder_id') or '(GOOGLE_FORMS_FOLDER_ID unset - required: service accounts own no storage)'}")
        self.stdout.write(f"       share forms with: {report['share_with'] or '(GOOGLE_FORMS_SHARE_WITH unset)'}")
        for key, msg in report["errors"].items():
            self.stdout.write(self.style.WARNING(f"  {key}: {msg}"))

        ready = report["token"] and report["forms_api"] and report["drive_api"]
        self.stdout.write(self.style.SUCCESS("\nREADY") if ready else self.style.ERROR("\nNOT READY"))

        job_id = options.get("create_for")
        if not job_id:
            return

        # Resolved even when not ready, so a wrong or missing slug is
        # reported now rather than after the credentials are fixed.
        organization = resolve_organization(options.get("organization"))
        with acting_as(None, organization=organization):
            self._create_for(organization, job_id, ready=ready, ef=ef)

    def _create_for(self, organization, job_id, *, ready, ef):
        from django.core.exceptions import ValidationError

        from apps.recruitment.models import JobOpening

        # Filtered by organization explicitly rather than trusting the
        # manager alone: the id came from a terminal, and the refusal must
        # not depend on which apps happen to filter at the manager today.
        try:
            job = JobOpening.objects.filter(organization=organization, pk=job_id).first()
        except ValidationError:
            job = None
        if job is None:
            raise CommandError(f"No job opening {job_id} in {organization.slug}.")

        if not ready:
            self.stdout.write(self.style.ERROR("Not creating a form: fix the above first."))
            return
        ef.create_external_form(job)
        job.refresh_from_db()
        self.stdout.write(f"{job.title}: provider={job.external_form_provider} id={job.external_form_id}")
        self.stdout.write(f"  form url: {job.external_form_url or '-'}")
        self.stdout.write(f"  hosted:   {job.application_url}")
        if job.external_form_error:
            self.stdout.write(self.style.ERROR(f"  error: {job.external_form_error}"))
