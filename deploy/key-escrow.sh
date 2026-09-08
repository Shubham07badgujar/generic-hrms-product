#!/usr/bin/env bash
#
# FIELD_ENCRYPTION_KEY custody helper.
#
# The key encrypts every PAN, Aadhaar and bank account in the database. Lose it
# and those columns are unreadable forever — there is no recovery path, and a
# database backup on its own does not contain it (deliberately: a backup
# holding both the ciphertext and its key is not an encrypted backup).
#
# It currently lives in exactly one file on one machine. That is a single point
# of total data loss, so it needs a copy somewhere this server cannot reach.
#
#   ./key-escrow.sh fingerprint   # print a SHA-256 fingerprint, never the key
#   ./key-escrow.sh verify        # check a copy you paste matches, without echoing it
#   ./key-escrow.sh reveal        # print the key ONCE, for you to store offsite
#
# `reveal` is separated deliberately. Everything else in this deployment avoids
# putting the key on a terminal; when you do need it, it should be a conscious
# act rather than a side effect of running a status command.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/hrms-v2/deploy/.env}"

key() {
    [ -f "$ENV_FILE" ] || { echo "FATAL: $ENV_FILE not found." >&2; exit 1; }
    local k
    k="$(grep '^FIELD_ENCRYPTION_KEY=' "$ENV_FILE" | cut -d= -f2- || true)"
    [ -n "$k" ] || { echo "FATAL: FIELD_ENCRYPTION_KEY is empty." >&2; exit 1; }
    printf '%s' "$k"
}

case "${1:-fingerprint}" in

fingerprint)
    # Safe to paste anywhere, including a ticket or a chat. It identifies the
    # key without revealing it, so an offsite copy can be checked against it.
    printf 'FIELD_ENCRYPTION_KEY fingerprint (SHA-256):\n  %s\n' \
        "$(key | sha256sum | awk '{print $1}')"
    printf 'length: %s characters\n' "$(key | wc -c | tr -d ' ')"
    ;;

verify)
    # Reads the candidate from the terminal with echo OFF, so the copy you are
    # checking never appears on screen, in scrollback, or in shell history.
    printf 'Paste the key you have stored offsite (input hidden): '
    read -rs candidate
    echo
    if [ "$(printf '%s' "$candidate" | sha256sum)" = "$(key | sha256sum)" ]; then
        echo "MATCH — your offsite copy is the live key."
    else
        echo "MISMATCH — your copy will NOT decrypt this database." >&2
        exit 1
    fi
    unset candidate
    ;;

reveal)
    # Guarded: refuses over a non-interactive session, so it cannot be captured
    # by a script, a CI job, or an agent piping output somewhere.
    if [ ! -t 1 ]; then
        echo "FATAL: refusing to print the key to a non-interactive session." >&2
        echo "Run this directly in your own terminal." >&2
        exit 1
    fi
    echo "About to print the encryption key to this terminal."
    echo "Store it in a password manager, then clear your scrollback."
    printf 'Type CONFIRM to continue: '
    read -r answer
    [ "$answer" = "CONFIRM" ] || { echo "Aborted."; exit 1; }
    echo
    key; echo
    echo
    echo "Stored? Then clear this terminal's history."
    ;;

*)
    echo "usage: $0 {fingerprint|verify|reveal}" >&2
    exit 2
    ;;
esac
