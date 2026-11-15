"""The one exception type the device layer raises to callers.

Its message is already translated (``core.i18n.t``) and written for a coach, so the UI can show
``str(exc)`` as is.
"""
from __future__ import annotations


class DeviceError(Exception):
    """A device could not be found, connected or controlled. ``str()`` is user-facing text."""
