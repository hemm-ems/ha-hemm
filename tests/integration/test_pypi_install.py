"""End-to-end install guard for the manifest-pinned PyPI core."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "custom_components" / "hemm" / "manifest.json"
HEMM_FLOW_DATA = {
    "name": "HEMM",
    "horizon_hours": 24,
    "max_iterations": 50,
    "price_adapter": "template",
    "solver_backend": "milp_central",
}


@pytest.mark.container
@pytest.mark.req("009:FR-003")
def test_manifest_pinned_core_installs_from_pypi_and_hub_loads(request: pytest.FixtureRequest) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    requirement = manifest["requirements"][0]
    package, _, version = requirement.partition("==")

    if not _pypi_has_version(package, version):
        pytest.skip(f"{requirement} is not resolvable on PyPI yet")

    hactl = request.getfixturevalue("hactl")
    _install_core_from_pypi(requirement, version)
    _restart_ha()

    result = hactl.config_flow_start("hemm")
    assert result.success
    flow_id = result.json_data["flow_id"]

    result = hactl.config_flow_step(flow_id, HEMM_FLOW_DATA)
    assert result.success
    assert result.json_data.get("type") in ("create_entry", "abort")

    result = hactl.config_entries()
    assert result.success
    entries = _entries_from_result(result.json_data)
    hemm_entries = [entry for entry in entries if entry.get("domain") == "hemm"]
    assert hemm_entries
    assert hemm_entries[0].get("state") == "loaded"


def _pypi_has_version(package: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return False


def _install_core_from_pypi(requirement: str, version: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "hemm-ha-test",
            "python3",
            "-m",
            "pip",
            "install",
            "--quiet",
            "--force-reinstall",
            "--no-deps",
            requirement,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    result = subprocess.run(
        [
            "docker",
            "exec",
            "hemm-ha-test",
            "python3",
            "-c",
            "import importlib.metadata as m; print(m.version('hemm'))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == version


def _restart_ha() -> None:
    subprocess.run(["docker", "restart", "hemm-ha-test"], check=True, capture_output=True, text=True, timeout=60)
    _wait_for_ha()


def _wait_for_ha(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request("http://127.0.0.1:8123/api/")
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    return
        except urllib.error.HTTPError as err:
            if err.code == 401:
                return
        except OSError:
            pass
        time.sleep(2)
    raise AssertionError("HA did not become ready after reinstalling the PyPI core")


def _entries_from_result(data: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        entries = data.get("entries", [])
        return [entry for entry in entries if isinstance(entry, dict)]
    return []
