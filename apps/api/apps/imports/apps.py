"""Candidate import — staging for externally sourced candidate data."""

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class ImportsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.imports"
    label = "imports"
    verbose_name = "Candidate import"

    def ready(self):
        # openpyxl swaps its XML backend for defusedxml's simply by finding the
        # package installed — no code change, and no error if it is missing.
        # That silence is the problem: without it, sheet XML is parsed by stdlib
        # ElementTree, which resists external entities but NOT entity-expansion
        # denial of service. This app parses attacker-supplied XML, so the
        # protection being present is a boot condition, not a preference.
        #
        # It is also disableable by environment variable, which is why this
        # asserts the effect rather than the dependency.
        import openpyxl

        if not getattr(openpyxl, "DEFUSEDXML", False):
            raise ImproperlyConfigured(
                "openpyxl is parsing XML with an undefended backend. Install "
                "defusedxml and leave OPENPYXL_DEFUSEDXML unset — candidate "
                "import parses spreadsheets supplied by strangers."
            )
