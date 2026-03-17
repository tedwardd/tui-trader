"""
Unit tests for app/notifications.py

Tests the send_notification function with mocked D-Bus proxy and GLib.
dasbus and gi.repository are mocked so these tests run without system
packages installed.
"""

import sys
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Mock gi.repository.GLib before any app import touches it
# ---------------------------------------------------------------------------

class _FakeVariant:
    """Minimal GLib.Variant stand-in that records the type and value."""
    def __init__(self, fmt: str, value):
        self._fmt = fmt
        self._value = value

    def get_byte(self) -> int:
        return self._value

    def __repr__(self):
        return f"Variant({self._fmt!r}, {self._value!r})"


_fake_glib = MagicMock()
_fake_glib.Variant.side_effect = _FakeVariant

_fake_gi = MagicMock()
_fake_gi.repository.GLib = _fake_glib

# Inject into sys.modules so `from gi.repository import GLib` resolves to our mock
sys.modules.setdefault("gi", _fake_gi)
sys.modules.setdefault("gi.repository", _fake_gi.repository)
sys.modules.setdefault("gi.repository.GLib", _fake_glib)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_proxy():
    proxy = MagicMock()
    proxy.Notify = MagicMock(return_value=1)
    return proxy


def call_send(proxy, title="Title", body="", urgency="normal", timeout_ms=8000):
    """Call send_notification with a patched proxy and return the Notify call args."""
    import importlib
    import app.notifications as notif_mod
    # Reset cached proxy so our mock is used
    notif_mod._proxy = None
    with patch("app.notifications._get_proxy", return_value=proxy):
        notif_mod.send_notification(title, body, urgency, timeout_ms)
    return proxy.Notify.call_args


# ---------------------------------------------------------------------------
# send_notification — argument mapping
# ---------------------------------------------------------------------------

class TestSendNotification:
    def test_calls_notify_with_correct_app_name(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, "Test Title")[0]
        assert args[0] == "tui-trader"

    def test_calls_notify_with_title_and_body(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, "Price Alert", "BTC/USD above $80,000")[0]
        assert args[3] == "Price Alert"
        assert args[4] == "BTC/USD above $80,000"

    def test_replaces_id_is_zero(self):
        proxy = make_mock_proxy()
        args = call_send(proxy)[0]
        assert args[1] == 0

    def test_default_timeout(self):
        proxy = make_mock_proxy()
        args = call_send(proxy)[0]
        assert args[7] == 8000

    def test_custom_timeout(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, timeout_ms=0)[0]
        assert args[7] == 0

    def test_normal_urgency_hint(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, urgency="normal")[0]
        assert args[6]["urgency"].get_byte() == 1

    def test_critical_urgency_hint(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, urgency="critical")[0]
        assert args[6]["urgency"].get_byte() == 2

    def test_low_urgency_hint(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, urgency="low")[0]
        assert args[6]["urgency"].get_byte() == 0

    def test_unknown_urgency_defaults_to_normal(self):
        proxy = make_mock_proxy()
        args = call_send(proxy, urgency="bogus")[0]
        assert args[6]["urgency"].get_byte() == 1

    def test_empty_body_default(self):
        proxy = make_mock_proxy()
        args = call_send(proxy)[0]
        assert args[4] == ""

    def test_dbus_error_does_not_raise(self):
        proxy = make_mock_proxy()
        proxy.Notify.side_effect = Exception("D-Bus unavailable")
        # Should not raise
        call_send(proxy)

    def test_proxy_unavailable_does_not_raise(self):
        import app.notifications as notif_mod
        notif_mod._proxy = None
        with patch("app.notifications._get_proxy", side_effect=Exception("no bus")):
            notif_mod.send_notification("Title", "Body")


# ---------------------------------------------------------------------------
# Urgency mapping
# ---------------------------------------------------------------------------

class TestUrgencyMapping:
    def test_urgency_values(self):
        from app.notifications import URGENCY
        assert URGENCY["low"] == 0
        assert URGENCY["normal"] == 1
        assert URGENCY["critical"] == 2
