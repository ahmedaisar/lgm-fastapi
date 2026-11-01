"""Typed portal errors so api/index.py can map them to HTTP statuses centrally.

Hierarchy:
    PortalError                     (502)
      ├── PortalLoginError          — CSRF/ajax-login/probe failures
      ├── PortalUpstreamError       — portal answered non-2xx / bad payload
      └── PortalNotConfigured       (503) — PORTAL_USERNAME/PASSWORD unset

The old code raised bare Exceptions and sniffed message text in send_error();
typed errors make the 502/503 mapping explicit and testable.
"""


class PortalError(Exception):
    """Base: any failure talking to the unihotel.org portal (maps to 502)."""


class PortalLoginError(PortalError):
    """Login could not be established (bad creds, CSRF missing, probe failed)."""


class PortalUpstreamError(PortalError):
    """Portal responded with an unexpected status / unparseable payload."""


class PortalNotConfigured(PortalError):
    """PORTAL_USERNAME / PORTAL_PASSWORD are not set (maps to 503)."""
