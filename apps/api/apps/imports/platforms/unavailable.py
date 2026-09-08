"""
Platforms we recruit through but cannot integrate with.

Registered rather than omitted, and each says why. Leaving them out entirely
would invite the assumption that support is merely pending; a stub adapter that
silently did nothing would be worse still.

Verified against each platform's own documentation, August 2026. None of these
is a coding problem, so none has a half-written adapter behind it.
"""

from __future__ import annotations

from .registry import PlatformSpec

INDEED = PlatformSpec(
    key="indeed",
    label="Indeed",
    available=False,
    unavailable_reason=(
        "Indeed's Job Sync API and Indeed Apply both require a signed Developer "
        "Agreement and formal partner approval, which takes roughly six weeks "
        "and is not self-service. Their XML feed route for organic postings "
        "closed on 31 March 2026. Until a partner agreement exists, export "
        "applicants from the Indeed employer dashboard and import the file."
    ),
)

LINKEDIN = PlatformSpec(
    key="linkedin",
    label="LinkedIn",
    available=False,
    unavailable_reason=(
        "LinkedIn states it is not currently accepting new partnerships for its "
        "Job Posting API, and offers no native bulk applicant export — "
        "applicants can only be viewed one at a time. Every tool claiming to "
        "export them is a scraping browser extension, which this system will "
        "not use. LinkedIn candidates must be entered manually."
    ),
)
