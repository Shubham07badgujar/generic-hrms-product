"""
One-time: let HRMS create Google Forms as a real Google account.

    manage.py google_forms_authorize

Prints a Google sign-in URL. The person who should OWN the job application
forms opens it, signs in, accepts the three scopes (edit forms, read
responses, files this app creates), and Google redirects their browser back
to a tiny listener on this machine, which captures the code, exchanges it for
a refresh token, and writes GOOGLE_FORMS_OAUTH_TOKEN_FILE. That file is a
secret; it is created 0600 and never printed.

Why not a service account: Google gives them no Drive storage, and a Google
Form is charged to its creator, so on a Gmail-based organisation a service
account cannot create forms at all. A person can.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.recruitment.services.external_forms import SCOPES, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class Command(BaseCommand):
    help = "Authorise HRMS to create Google Forms as a real Google account (one-time)."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument("--no-browser", action="store_true", help="Print the URL only.")
        parser.add_argument("--wait", type=int, default=300, help="Seconds to wait for the consent (default 300).")

    def handle(self, *args, **options):
        client_path = getattr(settings, "GOOGLE_FORMS_OAUTH_CLIENT_FILE", "")
        token_path = getattr(settings, "GOOGLE_FORMS_OAUTH_TOKEN_FILE", "")
        if not client_path or not token_path:
            raise CommandError(
                "Set GOOGLE_FORMS_OAUTH_CLIENT_FILE (OAuth client JSON, Desktop app) and "
                "GOOGLE_FORMS_OAUTH_TOKEN_FILE (where to write the grant) first."
            )
        with open(client_path, encoding="utf-8") as fh:
            client = json.load(fh)
        client = client.get("installed") or client.get("web") or client
        client_id, client_secret = client["client_id"], client.get("client_secret", "")

        port = options["port"]
        redirect = f"http://127.0.0.1:{port}/"
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
            "state": state,
        }
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] != state:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"state mismatch"); return
                result["code"] = q.get("code", [""])[0]
                result["error"] = q.get("error", [""])[0]
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(b"<h2>HRMS is authorised. You can close this tab.</h2>")

            def log_message(self, *a):  # quiet
                return

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        self.stdout.write("Open this URL in the browser of the Google account that should own the forms:\n")
        self.stdout.write(url + "\n")
        if not options["no_browser"]:
            webbrowser.open(url)
        self.stdout.write(f"Waiting for Google to redirect back (up to {options['wait'] // 60} minutes)...")
        thread.join(timeout=options["wait"])
        server.server_close()
        if not result.get("code"):
            raise CommandError(f"No authorisation received ({result.get('error') or 'timeout'}).")

        import requests

        resp = requests.post(TOKEN_URL, data={
            "code": result["code"], "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect, "grant_type": "authorization_code",
        }, timeout=20)
        if resp.status_code >= 400:
            raise CommandError(f"Token exchange failed: {resp.text[:300]}")
        data = resp.json()
        if not data.get("refresh_token"):
            raise CommandError("Google did not return a refresh token; revoke the app at "
                               "myaccount.google.com/permissions and run this again.")

        account = ""
        try:
            who = requests.get("https://www.googleapis.com/oauth2/v3/userinfo",
                               headers={"Authorization": f"Bearer {data['access_token']}"}, timeout=10)
            account = who.json().get("email", "") if who.ok else ""
        except Exception:  # noqa: BLE001
            pass

        payload = {"refresh_token": data["refresh_token"], "account": account, "scopes": list(SCOPES)}
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        self.stdout.write(self.style.SUCCESS(f"Authorised as {account or '(unknown)'}. Grant written to {token_path}."))
        self.stdout.write("Now run: manage.py google_forms_check")
