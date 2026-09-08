"""
Sign-in rate limiting.

Two independent throttles guard the login endpoints, and a request has to
satisfy BOTH. They answer different questions:

  * `LoginClientThrottle` — how much may one client machine try?
    Keyed on the caller's IP, resolved through the proxies (see NUM_PROXIES in
    the production settings; without it every visitor resolves to the nginx
    container and the whole organisation shares one budget).

  * `LoginAccountThrottle` — how much may one account be tried?
    Keyed on the submitted email address, so the ceiling follows the account
    being attacked rather than the machine doing the attacking.

The per-account throttle is the reason the per-client limit can be raised
without simply handing an attacker more guesses. Before it existed, the only
ceiling was per-IP, which meant a distributed attempt against a single account
— a botnet, or anything with a pool of addresses — got a fresh allowance for
every address it owned and was, in aggregate, unlimited. Now one account cannot
be tried more than its own rate per hour no matter how many machines take part.

Neither throttle distinguishes a correct password from a wrong one, on purpose.
Counting only failures lets an attacker reset their budget with one known-good
login, and it makes the two responses observably different, which is its own
disclosure.
"""

from __future__ import annotations

import hashlib

from rest_framework.exceptions import ParseError
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class LoginClientThrottle(ScopedRateThrottle):
    """
    Per calling client, using the view's `throttle_scope` ("login").

    Deliberately a plain ScopedRateThrottle subclass rather than a rename: it
    exists so the login views can name both throttles explicitly, and so this
    module is the one place to look when asking how sign-in is limited.
    """


class LoginAccountThrottle(SimpleRateThrottle):
    """Per account being signed in to, regardless of where the attempt is from."""

    scope = "login_account"

    def get_cache_key(self, request, view):
        try:
            data = request.data
        except ParseError:
            # Unparseable body. The serializer will reject it; there is no
            # account to attribute the attempt to, and inventing a key here
            # would let malformed requests fill the cache with junk.
            return None

        email = data.get("email") if hasattr(data, "get") else None
        if not isinstance(email, str):
            return None

        # Normalised before hashing. Addresses are matched case-insensitively
        # at login, so counting "Admin@x" separately from "admin@x" would let
        # an attacker multiply their budget by varying the capitalisation.
        email = email.strip().lower()
        if not email:
            return None

        # Hashed, so Redis holds no list of the addresses people are trying to
        # sign in to. The cache is not the place for a directory of accounts,
        # and a throttle only needs the key to be stable, not readable.
        ident = hashlib.sha256(email.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": ident}
