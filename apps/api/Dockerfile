# HRMS API.
#
# Two stages so the runtime image carries no build toolchain. `psycopg[binary]`
# ships wheels, but reportlab and cryptography can fall back to compiling, and
# a compiler left in a production image is attack surface for no benefit.

# ---------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.lock.txt .

# The lockfile, not requirements.txt: transitive versions are pinned there, so
# an image built today and one built in six months are the same image.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.lock.txt

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# libpq for psycopg, curl for the container healthcheck. Nothing else.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# An unprivileged user. The app writes only to the media volume and staticfiles.
RUN useradd --system --create-home --uid 10001 hrms

WORKDIR /app
COPY --chown=hrms:hrms . .

# Media lives on a mounted volume; staticfiles is built at start-up.
RUN mkdir -p /var/lib/hrms/media /app/staticfiles \
    && chown -R hrms:hrms /var/lib/hrms /app/staticfiles

# NOTE: `deploy/entrypoint.sh` is NOT in this repository, so this image does
# not build as it stands. That was already true before the `deploy/` directory
# was removed -- the entrypoint and the Caddyfile were hand-maintained on the
# server, never committed here. Supported deployment is `render.yaml` plus
# `docs/DEPLOY_ON_RENDER.md`, which uses the Python runtime and no image at
# all. To revive this path, write an entrypoint that migrates as the OWNER
# role (see render/release-api.sh -- `migrate` refuses under the application
# role) and then execs the CMD below.
COPY --chown=hrms:hrms deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER hrms
EXPOSE 8000

# The healthcheck has to look like a real proxied request, or it fails for two
# reasons that have nothing to do with the app being unwell:
#
#   Host              — ALLOWED_HOSTS is the public domain, so a request to
#                       127.0.0.1 is rejected with 400.
#   X-Forwarded-Proto — SECURE_SSL_REDIRECT is on, so without it Django sees
#                       plain http and answers 301.
#
# Both settings are correct and stay as they are; the probe is what was wrong.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS \
        -H "Host: ${HEALTHCHECK_HOST:-127.0.0.1}" \
        -H "X-Forwarded-Proto: https" \
        http://127.0.0.1:8000/api/v1/health/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Two workers, two threads. The box is shared with ~25 other containers and has
# under 5 GB free — a default worker count derived from CPU cores would be a
# poor neighbour.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
