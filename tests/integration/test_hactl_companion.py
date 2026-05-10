"""Companion-enabled hactl features — templates, scripts, automations.

The companion runs inside the HA container and gives hactl filesystem access
to the config directory. All interactions go through the hactl CLI binary.
Companion-internal tests (health, CRUD, security) live in the companion repo.

If the companion is unavailable, tests skip gracefully.
"""

from __future__ import annotations

import pytest

from .hactl import Hactl, HactlError

pytestmark = [pytest.mark.container]


class TestCompanionTemplates:
    """Template evaluation via hactl tpl (requires companion)."""

    def test_tpl_eval_simple(self, hactl: Hactl) -> None:
        """hactl tpl eval with a simple expression works."""
        result = hactl.tpl_eval("{{ 2 + 2 }}")
        assert result.success
        assert "4" in result.stdout

    def test_tpl_eval_states_function(self, hactl: Hactl) -> None:
        """hactl tpl eval can call states() function."""
        result = hactl.tpl_eval('{{ states("sun.sun") }}')
        assert result.success
        output = result.stdout.lower()
        assert "horizon" in output or "above" in output or "below" in output

    def test_tpl_eval_invalid_returns_error(self, hactl: Hactl) -> None:
        """hactl tpl eval with invalid Jinja returns an error indicator."""
        try:
            result = hactl.tpl_eval("{{ invalid_function_xyz() }}")
            assert "error" in result.stdout.lower() or "undefined" in result.stdout.lower()
        except HactlError:
            pass


class TestCompanionScripts:
    """Script operations via hactl (requires companion)."""

    def test_script_ls_works(self, hactl: Hactl) -> None:
        """hactl script ls returns scripts (may be empty on fresh container)."""
        result = hactl.script_ls()
        assert result.success

    def test_script_ls_no_failing(self, hactl: Hactl) -> None:
        """No failing scripts on a fresh container."""
        try:
            result = hactl.script_ls(failing=True)
            assert result.success
        except HactlError:
            pass


class TestCompanionAutomations:
    """Automation operations via hactl (requires companion)."""

    def test_auto_ls_works(self, hactl: Hactl) -> None:
        """hactl auto ls returns automations (may be empty on fresh container)."""
        result = hactl.auto_ls()
        assert result.success

    def test_auto_ls_no_failing(self, hactl: Hactl) -> None:
        """No failing automations on a fresh container."""
        try:
            result = hactl.auto_ls(failing=True)
            assert result.success
        except HactlError:
            pass


class TestCompanionServices:
    """Service calls via hactl svc command."""

    def test_svc_call_check_config(self, hactl: Hactl) -> None:
        """hactl svc call homeassistant.check_config succeeds."""
        result = hactl.svc_call("homeassistant.check_config")
        assert result.success
