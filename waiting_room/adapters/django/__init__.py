"""Django plug-and-play adapter.

Add ``"waiting_room.adapters.django"`` to ``INSTALLED_APPS`` and
``"waiting_room.adapters.django.middleware.WaitingRoomMiddleware"`` to
``MIDDLEWARE``. Configure with the ``WAITING_ROOM`` settings dict, then run
``manage.py migrate`` to create the audit-log table.

Public API:

* ``waiting_room_protect`` — decorator for individual views
* ``WaitingRoomMiddleware`` — protects whole URL prefixes
* ``get_room`` — fetch the configured ``WaitingRoom`` engine instance
* ``waiting_room_event`` — Django ``Signal`` fired on every lifecycle event
"""

from waiting_room.adapters.django.decorators import waiting_room_protect
from waiting_room.adapters.django.registry import get_room
from waiting_room.adapters.django.signals import waiting_room_event

# default_app_config is deprecated since Django 3.2 — Django auto-discovers
# the AppConfig from the package's ``apps`` module.

default_app_config = "waiting_room.adapters.django.apps.WaitingRoomConfig"

__all__ = [
    "default_app_config",
    "get_room",
    "waiting_room_event",
    "waiting_room_protect",
]
