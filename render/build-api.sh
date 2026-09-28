#!/usr/bin/env bash
#
# Render build step for the API, the worker and the scheduler.
#
# Deliberately does NOT migrate. A build runs once per service, so three
# services building would race three `migrate` commands against one database --
# and on Render a build also runs for a deploy that is then rolled back. The
# migration is its own step: render/release-api.sh.
set -euo pipefail

echo "--- installing dependencies"
python -m pip install --upgrade pip
pip install -r requirements/base.txt

echo "--- collecting static files"
# Whitenoise serves these; the SPA is a separate static site. Django's own
# admin and DRF's browsable API are what actually need them.
python manage.py collectstatic --noinput

echo "--- checks"
# `check --deploy` is the one that catches a missing ALLOWED_HOSTS, a debug
# setting left on, or a weak cookie flag. It is cheap and it runs before
# anything is serving.
python manage.py check --deploy

echo "--- build complete"
