"""Infrastructure endpoints."""

from __future__ import annotations

from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """
    Liveness probe.

    Verifies a real database round-trip, not just that the process is up — a
    web server answering 200 while the database is unreachable is worse than
    no health check at all.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    access_exempt = True

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            database = "ok"
        except Exception:
            database = "unavailable"

        status_code = 200 if database == "ok" else 503
        return Response({"status": "ok" if database == "ok" else "degraded",
                         "database": database}, status=status_code)
