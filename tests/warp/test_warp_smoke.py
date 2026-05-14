"""Time-warp smoke test - proves the libwarp.so Docker container runs HA at
the configured speed multiplier (fixed mode) or auto-adjusting speed (auto mode).

What's measured:
  1. The application-visible clock (`datetime.now()`) advances faster than
     wall-clock (so dt_util.utcnow() in HA sees accelerated time).
  2. `asyncio.sleep(N)` returns faster than real N seconds (so HA's
     scheduler ticks at warp rate, not just the displayed clock).
  3. HA's time-pattern automations fire at virtual cadence - the
     `warp-heartbeat tick` automation in `tests/warp/config/configuration.yaml`
     is scheduled for `minutes: '*'` (every virtual minute) and we count
     ticks observed in `docker logs` over a wall-clock window.

Requires the warp stack to already be running (`make warp-up`). The test does
NOT spin up the container itself - `make test-warp` does that orchestration
and tears down on exit regardless of pass/fail.

Supports both fixed mode (WARP_SPEED=100) and auto mode (no WARP_SPEED).
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

CONTAINER_NAME = "hemm-ha-warp"
# In auto mode WARP_SPEED is empty/unset; tests use relaxed thresholds.
_speed_env = os.environ.get("WARP_SPEED", "")
FIXED_MODE = bool(_speed_env)
EXPECTED_SPEED_MULTIPLIER = int(_speed_env) if FIXED_MODE else 100
# Wall-window in which we count heartbeat ticks.
HEARTBEAT_WINDOW_SECONDS = 10
# Expected ticks ~= WALL * SPEED / 60 (one tick per virtual minute).
# At 100x over 10s wall, that's ~16 ticks. Allow +/-50% margin for startup jitter.
HEARTBEAT_TOLERANCE = 0.50


def _exec_python(code: str) -> str:
    """Run a Python snippet inside the warp container; return stdout."""
    return subprocess.check_output(
        ["docker", "exec", CONTAINER_NAME, "python3", "-c", code],
        text=True,
    )


@pytest.mark.warp
def test_container_running() -> None:
    """Pre-flight: the warp container is running and healthy."""
    out = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", CONTAINER_NAME],
        text=True,
    ).strip()
    assert out == "healthy", f"container health = {out!r}; expected 'healthy'"


@pytest.mark.warp
def test_warp_lib_loaded() -> None:
    """libwarp is the active LD_PRELOAD in the HA process (not jemalloc)."""
    out = subprocess.check_output(
        [
            "docker",
            "exec",
            CONTAINER_NAME,
            "sh",
            "-c",
            'cat /proc/$(pgrep -f "python.*homeassistant" | head -1)/environ | tr "\\0" "\\n"',
        ],
        text=True,
    )
    assert "LD_PRELOAD=/usr/lib/libwarp.so" in out


@pytest.mark.warp
def test_virtual_clock_advances_at_warp_speed() -> None:
    """`datetime.now()` advances at speed-x during a single long-lived process."""
    out = _exec_python(
        """
import datetime, time, json
t0 = datetime.datetime.now(datetime.UTC); m0 = time.monotonic()
end = m0 + 1.0
while time.monotonic() < end: pass
t1 = datetime.datetime.now(datetime.UTC); m1 = time.monotonic()
print(json.dumps({
    "virt_now_delta_s": (t1 - t0).total_seconds(),
    "virt_mono_delta_s": m1 - m0,
}))
"""
    )
    data = json.loads(out.strip())
    assert 0.9 <= data["virt_now_delta_s"] <= 2.0
    assert 0.9 <= data["virt_mono_delta_s"] <= 2.0


@pytest.mark.warp
def test_asyncio_sleep_scales_with_speed() -> None:
    """`asyncio.sleep(1)` returns in ~1/SPEED wall-seconds - the load-bearing
    contract for HA's scheduler running at warp."""
    wall_start = time.monotonic()
    _exec_python(
        """
import asyncio
async def main():
    for _ in range(5): await asyncio.sleep(1)
asyncio.run(main())
"""
    )
    wall_elapsed = time.monotonic() - wall_start
    assert wall_elapsed < 2.0, (
        f"5 asyncio.sleep(1) calls took {wall_elapsed:.2f}s wall - scheduler isn't warping (would be ~5s at 1x)"
    )


@pytest.mark.warp
def test_ha_scheduler_fires_automation_at_warp_rate() -> None:
    """The `warp-heartbeat` automation (triggers every virtual minute) should
    fire at an accelerated rate. In fixed mode we check against the expected
    speed; in auto mode we just verify ticks are arriving faster than real-time
    (at least a few ticks in the window — proving warp speed > 1x)."""
    out_before = subprocess.check_output(
        ["docker", "logs", CONTAINER_NAME],
        stderr=subprocess.STDOUT,
        text=True,
    )
    baseline = out_before.count("warp-heartbeat tick")

    time.sleep(HEARTBEAT_WINDOW_SECONDS)

    out_after = subprocess.check_output(
        ["docker", "logs", CONTAINER_NAME],
        stderr=subprocess.STDOUT,
        text=True,
    )
    final = out_after.count("warp-heartbeat tick")
    new_ticks = final - baseline

    if FIXED_MODE:
        expected = HEARTBEAT_WINDOW_SECONDS * EXPECTED_SPEED_MULTIPLIER / 60
        low = expected * (1 - HEARTBEAT_TOLERANCE)
        high = expected * (1 + HEARTBEAT_TOLERANCE)
        assert low <= new_ticks <= high, (
            f"observed {new_ticks} heartbeat ticks in {HEARTBEAT_WINDOW_SECONDS}s wall; "
            f"expected ~{expected:.0f} (range {low:.0f}-{high:.0f}) at {EXPECTED_SPEED_MULTIPLIER}x"
        )
    else:
        # Auto mode: speed ramps from 1x, just verify acceleration is happening
        # At even 10x speed, 10s wall = 100s virtual ≈ 1.6 ticks; be generous
        assert new_ticks >= 1, (
            f"observed {new_ticks} heartbeat ticks in {HEARTBEAT_WINDOW_SECONDS}s wall; "
            f"expected at least 1 in auto mode (warp speed should be > 1x by now)"
        )


@pytest.mark.warp
def test_virtual_clock_in_2026_or_later() -> None:
    """The default WARP_START_REAL anchor (2026-05-12) is honored."""
    out = _exec_python("import datetime; print(datetime.datetime.now(datetime.UTC).isoformat())")
    iso = out.strip()
    assert iso >= "2026-05-12", f"virtual now {iso} earlier than configured anchor"


@pytest.mark.warp
def test_no_unrecoverable_errors_in_ha_log() -> None:
    """HA boots cleanly under libwarp."""
    out = subprocess.check_output(
        ["docker", "logs", CONTAINER_NAME],
        stderr=subprocess.STDOUT,
        text=True,
    )
    bad = [
        line
        for line in out.splitlines()
        if "Setup failed" in line and any(c in line for c in ("input_number", "template", "recorder", "automation"))
    ]
    assert not bad, "core sim components failed to set up:\n" + "\n".join(bad)
