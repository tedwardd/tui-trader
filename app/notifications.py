"""
OS-level desktop notifications via D-Bus.

Sends notifications directly to org.freedesktop.Notifications over the
session D-Bus using dasbus — no subprocess, no shell, pure Python.

Requirements (system packages, not pip):
    sudo pacman -S python-dasbus python-gobject

Falls back silently if D-Bus is unavailable (e.g. in CI or headless tests).

Urgency levels (freedesktop spec):
    "low"      — 0 — dimmed, short timeout
    "normal"   — 1 — standard
    "critical" — 2 — highlighted, does not auto-expire in Mako
"""

from typing import Optional

# Urgency byte values per the freedesktop notifications spec
URGENCY: dict[str, int] = {
    "low": 0,
    "normal": 1,
    "critical": 2,
}

_proxy = None


def _get_proxy():
    """Return a cached D-Bus proxy to org.freedesktop.Notifications."""
    global _proxy
    if _proxy is None:
        from dasbus.connection import SessionMessageBus
        _proxy = SessionMessageBus().get_proxy(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
        )
    return _proxy


def send_notification(
    title: str,
    body: str = "",
    urgency: str = "normal",
    timeout_ms: int = 8000,
) -> None:
    """
    Send a desktop notification via D-Bus.

    The notification appears in the OS notification daemon (Mako on
    Hyprland) regardless of whether the terminal is focused or minimised.

    Args:
        title:      Notification summary / title line.
        body:       Optional detail text shown below the title.
        urgency:    "low" | "normal" | "critical".
                    Critical notifications do not auto-expire.
        timeout_ms: Milliseconds before auto-dismiss. 0 = never expire.
                    Ignored for critical urgency (daemon keeps it visible).
    """
    try:
        from gi.repository import GLib

        urgency_byte = URGENCY.get(urgency, URGENCY["normal"])
        hints = {"urgency": GLib.Variant("y", urgency_byte)}

        _get_proxy().Notify(
            "tui-trader",   # app_name
            0,               # replaces_id  (0 = new notification)
            "dialog-warning",  # app_icon
            title,           # summary
            body,            # body
            [],              # actions
            hints,           # hints
            timeout_ms,      # expire_timeout (ms)
        )
    except Exception:
        # Silently degrade — D-Bus may be unavailable in CI / headless envs.
        # The in-terminal Textual toast is always shown regardless.
        pass
