"""
ASGI entry point.

Notifications are polled over REST in V1 (see docs/ARCHITECTURE.md §5). ASGI is
configured now so a WebSocket transport can be added later by wiring a
ProtocolTypeRouter here — no other layer needs to change.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_asgi_application()
