"""
Unit tests for app/cloud_sync.py — is_configured and basic module behavior.
"""

import pytest
from app.cloud_sync import is_configured


class TestCloudSyncConfigured:
    def test_is_configured_returns_bool(self):
        result = is_configured()
        assert isinstance(result, bool)

    def test_is_configured_is_false_by_default(self):
        # Without CLOUD_SYNC_ENABLED, should return False
        assert is_configured() is False
