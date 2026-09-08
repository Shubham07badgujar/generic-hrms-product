"""
Is Google Forms integration ready? One command, plain answers.

    manage.py google_forms_check
    manage.py google_forms_check --create-for <job id>   # build the form for one job now

Reads GOOGLE_FORMS_CREDENTIALS_FILE, mints a token, and tries each API the
integration needs. Prints what works and, for what does not, the sentence an
administrator needs to fix it. Leaves nothing behind in the Google account.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check Google Forms credentials and API access; optionally build a job's form."

    def add_arguments(self, parser):
        parser.add_argument("--create-for", metavar="JOB_ID", help="Create the Google Form for this job now.")

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
        if job_id:
            from apps.recruitment.models import JobOpening

            job = JobOpening.objects.get(pk=job_id)
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
