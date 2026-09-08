# HRMS SPA.
#
# Built to static files and served by a tiny nginx. The shared Caddy on this
# host terminates TLS and routes to it, so nginx here does nothing but serve
# files and hand unknown paths back to index.html for client-side routing.

# ---------------------------------------------------------------- builder
FROM node:22-alpine AS builder

WORKDIR /build

# Dependencies first, so a source-only change does not reinstall them.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# The SPA calls /api/v1 on its own origin; Caddy routes both to the right
# place, so there is no API host baked into the bundle.
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
