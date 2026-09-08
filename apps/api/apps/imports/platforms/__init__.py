"""
Platform adapters, as a registry rather than a WorkIndia special case.

Adding a platform is a `PlatformSpec` in this package. Nothing in the parser,
the deduplicator or the API knows a platform by name.

Supported by file import: WorkIndia, Naukri.com and Internshala — each spec
verified against a real export of that platform's dashboard (August 2026).

AVAILABILITY IS PART OF THE SPEC
--------------------------------
Two platforms cannot be supported at all, and the honest thing is to say why
rather than to omit them and let someone assume the integration is coming.
Each carries `available=False` and the actual reason, which the API returns
and the UI shows.

Verified against each platform's own documentation in August 2026:

  Indeed       Job Sync API and Indeed Apply both require a signed Developer
               Agreement and partner approval (~6 weeks). Their XML feed route
               for organic postings closed on 31 March 2026.
  LinkedIn     "We are currently not accepting new partnerships for LinkedIn's
               Job Posting API" — stated in the current Talent Solutions docs.
               There is also no native bulk applicant export at all; every tool
               offering one is a scraping browser extension, which is out of
               scope by policy and by their terms.

Neither is a coding problem, so neither has a half-written adapter.
"""

from __future__ import annotations

from .internshala import INTERNSHALA
from .naukri import NAUKRI
from .registry import ColumnSpec, PlatformSpec, get_platform, list_platforms, register
from .unavailable import INDEED, LINKEDIN
from .workindia import WORKINDIA

for _spec in (WORKINDIA, NAUKRI, INTERNSHALA, INDEED, LINKEDIN):
    register(_spec)

__all__ = [
    "ColumnSpec",
    "PlatformSpec",
    "get_platform",
    "list_platforms",
    "register",
    "WORKINDIA",
    "NAUKRI",
    "INDEED",
    "INTERNSHALA",
    "LINKEDIN",
]
