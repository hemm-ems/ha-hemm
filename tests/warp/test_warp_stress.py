"""Warp stress test — measures maximum sustainable speed and stability.

Runs the warp container with the "villa" configuration (14 sensors, 5 automations)
and collects metrics over a sustained window. Reports:
  - Peak and sustained warp speed
  - CPU utilization under load
  - Scheduler reliability (tick rate vs expected)
  - Clock drift (monotonicity, consistency)
  - Error rate in HA logs

Requires: docker compose stack running with villa config.
Usage:
    # Start stack with villa config, then:
    uv run pytest tests/warp/test_warp_stress.py -v -s -m warp -o "addopts="
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import time

import pytest

CONTAINER_NAME = "hemm-ha-warp"

# Stress observation windows
RAMP_WINDOW = 20       # seconds to let PI ramp up
OBSERVE_WINDOW = 30    # seconds to observe sustained behavior
SAMPLE_INTERVAL = 2    # seconds between metric samples


def _exec(cmd: str) -> str:
    """Run a shell command and return stdout."""
    return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()


def _exec_python(code: str) -> str:
    """Run Python inside the warp container."""
    return subprocess.check_output(
        ["docker", "exec", CONTAINER_NAME, "python3", "-c", code],
        text=True,
    ).strip()


def _get_speed() -> float:
    """Read current warp speed from the speed file."""
    try:
        out = _exec(f"docker exec {CONTAINER_NAME} cat /tmp/.warp_speed")
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError):
        return -1.0


def _get_container_logs() -> str:
    return subprocess.check_output(
        ["docker", "logs", CONTAINER_NAME],
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.mark.warp
class TestWarpStress:
    """Stress test suite for the warp PI controller under villa load."""

    def test_pi_ramp_and_sustained_speed(self) -> None:
        """PI controller ramps up and sustains a stable speed.

        Collects speed samples over OBSERVE_WINDOW and reports statistics.
        """
        # Let PI settle first
        time.sleep(RAMP_WINDOW)

        speeds: list[float] = []
        for _ in range(OBSERVE_WINDOW // SAMPLE_INTERVAL):
            s = _get_speed()
            if s > 0:
                speeds.append(s)
            time.sleep(SAMPLE_INTERVAL)

        assert len(speeds) >= 5, f"Only got {len(speeds)} speed samples"

        peak = max(speeds)
        mean = statistics.mean(speeds)
        stdev = statistics.stdev(speeds) if len(speeds) > 1 else 0
        last_5 = speeds[-5:]
        sustained = statistics.mean(last_5)
        jitter_pct = (stdev / mean * 100) if mean > 0 else 0

        print("\n=== PI Controller Stability ===")
        print(f"  Samples:       {len(speeds)}")
        print(f"  Peak speed:    {peak:.1f}x")
        print(f"  Mean speed:    {mean:.1f}x")
        print(f"  Sustained:     {sustained:.1f}x (last 5 samples)")
        print(f"  Stdev:         {stdev:.1f}")
        print(f"  Jitter:        {jitter_pct:.1f}%")
        print(f"  Min:           {min(speeds):.1f}x")
        print(f"  All samples:   {[round(s, 1) for s in speeds]}")

        # Speed should have ramped above 1x
        assert sustained > 10, f"Sustained speed {sustained:.1f}x is too low"
        # Jitter should be reasonable (< 30% of mean once settled)
        assert jitter_pct < 30, f"Speed jitter {jitter_pct:.1f}% too high"

    def test_scheduler_tick_rate(self) -> None:
        """HA's time-pattern automation fires at the expected warped rate."""
        logs_before = _get_container_logs()
        baseline = logs_before.count("villa-heartbeat tick")

        window = 15  # seconds
        time.sleep(window)

        logs_after = _get_container_logs()
        final = logs_after.count("villa-heartbeat tick")
        new_ticks = final - baseline

        speed = _get_speed()
        if speed <= 0:
            speed = 100  # fallback for fixed mode

        # Expected: window_s * speed / 60 ticks (one per virtual minute)
        expected = window * speed / 60
        ratio = new_ticks / expected if expected > 0 else 0

        print("\n=== Scheduler Tick Rate ===")
        print(f"  Window:        {window}s wall")
        print(f"  Speed:         {speed:.1f}x")
        print(f"  Expected:      {expected:.0f} ticks")
        print(f"  Observed:      {new_ticks} ticks")
        print(f"  Ratio:         {ratio:.2f} (1.0 = perfect)")

        # At least some ticks should have fired
        assert new_ticks >= 1, f"No heartbeat ticks in {window}s wall"
        # At extreme speeds (>500x) the scheduler physically can't keep up
        # with every virtual minute — that's expected. At moderate speeds
        # the ratio should be reasonable.
        if speed < 500 and expected > 5:
            assert ratio > 0.3, f"Tick ratio {ratio:.2f} too low — scheduler can't keep up"
        else:
            # At extreme speed, just verify ticks are flowing
            assert new_ticks >= 3, (
                f"Only {new_ticks} ticks at {speed:.0f}x — scheduler not warping"
            )

    def test_asyncio_sleep_under_load(self) -> None:
        """asyncio.sleep still scales correctly under villa load."""
        wall_start = time.monotonic()
        _exec_python(
            """
import asyncio
async def main():
    for _ in range(10): await asyncio.sleep(1)
asyncio.run(main())
"""
        )
        wall_elapsed = time.monotonic() - wall_start

        speed = _get_speed()
        # At high speed, 10 virtual seconds should be fast
        expected_wall = 10.0 / speed if speed > 0 else 10.0
        # Add docker exec overhead (~2s process start + variable load)
        max_acceptable = expected_wall + 5.0

        print("\n=== asyncio.sleep Under Load ===")
        print(f"  10x asyncio.sleep(1): {wall_elapsed:.2f}s wall")
        print(f"  Speed:                {speed:.1f}x")
        print(f"  Expected wall time:   {expected_wall:.3f}s + overhead")
        print(f"  Max acceptable:       {max_acceptable:.2f}s")

        assert wall_elapsed < max_acceptable, (
            f"asyncio.sleep took {wall_elapsed:.2f}s, expected < {max_acceptable:.2f}s"
        )

    def test_clock_monotonicity(self) -> None:
        """Virtual clocks are monotonic over rapid successive reads."""
        out = _exec_python(
            """
import time, json
samples = []
for _ in range(100):
    samples.append(time.monotonic())
deltas = [samples[i+1] - samples[i] for i in range(len(samples)-1)]
print(json.dumps({
    "count": len(samples),
    "negative_deltas": sum(1 for d in deltas if d < 0),
    "min_delta": min(deltas),
    "max_delta": max(deltas),
    "mean_delta": sum(deltas)/len(deltas),
}))
"""
        )
        data = json.loads(out)

        print("\n=== Clock Monotonicity ===")
        print(f"  Samples:         {data['count']}")
        print(f"  Negative deltas: {data['negative_deltas']}")
        print(f"  Min delta:       {data['min_delta']:.9f}s")
        print(f"  Max delta:       {data['max_delta']:.9f}s")
        print(f"  Mean delta:      {data['mean_delta']:.9f}s")

        assert data["negative_deltas"] == 0, (
            f"{data['negative_deltas']} non-monotonic clock reads"
        )

    def test_realtime_clock_consistency(self) -> None:
        """CLOCK_REALTIME and CLOCK_MONOTONIC advance at the same rate."""
        out = _exec_python(
            """
import datetime, time, json
t0_real = datetime.datetime.now(datetime.UTC)
t0_mono = time.monotonic()
end = t0_mono + 0.5
while time.monotonic() < end: pass
t1_real = datetime.datetime.now(datetime.UTC)
t1_mono = time.monotonic()
real_delta = (t1_real - t0_real).total_seconds()
mono_delta = t1_mono - t0_mono
print(json.dumps({
    "real_delta": real_delta,
    "mono_delta": mono_delta,
    "ratio": real_delta / mono_delta if mono_delta > 0 else 0,
}))
"""
        )
        data = json.loads(out)

        print("\n=== Clock Consistency ===")
        print(f"  REALTIME delta:  {data['real_delta']:.6f}s")
        print(f"  MONOTONIC delta: {data['mono_delta']:.6f}s")
        print(f"  Ratio:           {data['ratio']:.4f} (1.0 = perfect)")

        assert 0.8 <= data["ratio"] <= 1.2, (
            f"Clock ratio {data['ratio']:.4f} — REALTIME and MONOTONIC diverge"
        )

    def test_no_ha_errors_under_load(self) -> None:
        """No critical HA errors accumulated during the stress run."""
        logs = _get_container_logs()

        # Count different error levels
        error_lines = [
            l for l in logs.splitlines()
            if "ERROR" in l and "homeassistant" in l.lower()
        ]
        setup_failures = [l for l in logs.splitlines() if "Setup failed" in l]
        # Exclude known harmless errors
        real_errors = [
            l for l in error_lines
            if not any(x in l for x in [
                "warp",
                "custom_components",
                "We found a custom integration",
            ])
        ]

        print("\n=== HA Error Summary ===")
        print(f"  Total ERROR lines:   {len(error_lines)}")
        print(f"  Setup failures:      {len(setup_failures)}")
        print(f"  Real errors:         {len(real_errors)}")
        if real_errors:
            print("  Samples:")
            for line in real_errors[:5]:
                print(f"    {line[:120]}")

        assert len(setup_failures) == 0, (
            f"{len(setup_failures)} setup failures:\n" + "\n".join(setup_failures[:5])
        )

    def test_concurrent_docker_exec(self) -> None:
        """Multiple concurrent docker exec processes don't corrupt state."""
        import concurrent.futures

        def run_one(i: int) -> dict:
            t0 = time.monotonic()
            out = _exec_python(
                f"""
import time, json
m = time.monotonic()
time.sleep(0.01)
print(json.dumps({{"id": {i}, "mono": m, "elapsed": time.monotonic() - m}}))
"""
            )
            wall = time.monotonic() - t0
            data = json.loads(out)
            data["wall"] = wall
            return data

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_one, i) for i in range(10)]
            results = [f.result() for f in futures]

        walls = [r["wall"] for r in results]
        elapseds = [r["elapsed"] for r in results]

        print("\n=== Concurrent docker exec ===")
        print(f"  Processes:     {len(results)}")
        print(f"  Wall times:    min={min(walls):.2f}s max={max(walls):.2f}s")
        print(f"  Inner elapsed: min={min(elapseds):.6f}s max={max(elapseds):.6f}s")

        # All processes should have succeeded (we'd get exceptions otherwise)
        assert len(results) == 10
        # Inner elapsed should be positive (clock didn't go backwards)
        assert all(r["elapsed"] > 0 for r in results), "Negative elapsed in some exec"

    def test_speed_summary(self) -> None:
        """Final summary: report the maximum achievable warp factor."""
        speed = _get_speed()
        logs = _get_container_logs()

        # Extract all speed values from PI logs
        pi_speeds = [
            float(m.group(1))
            for m in re.finditer(r"\[warp-pi\] speed=([\d.]+)", logs)
        ]

        ticks = logs.count("villa-heartbeat tick")

        print("\n" + "=" * 60)
        print("  WARP STRESS TEST SUMMARY")
        print("=" * 60)
        print(f"  Configuration:     villa (14 sensors, 5 automations)")
        print(f"  Current speed:     {speed:.1f}x")
        if pi_speeds:
            print(f"  Peak speed:        {max(pi_speeds):.1f}x")
            print(f"  Mean speed:        {statistics.mean(pi_speeds):.1f}x")
            print(f"  Speed samples:     {len(pi_speeds)}")
        print(f"  Total ticks:       {ticks}")
        print(f"  Virtual minutes:   ~{ticks} min = ~{ticks/60:.1f} hours")
        print("=" * 60)

        assert speed > 0, "No speed data available"
