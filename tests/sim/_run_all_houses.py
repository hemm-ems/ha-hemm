"""Run all 5 sim houses: start → install hemm → onboard → setup → monitor 5 min.

Usage:
    uv run python tests/sim/_run_all_houses.py
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_LOG = logging.getLogger(__name__)

COMPOSE_FILE = str(Path(__file__).parent / "docker-compose.sim.yml")
BIN_DIR = Path(__file__).parent.parent.parent / ".bin"

HOUSES = [
    ("starter", 8130),
    ("family", 8131),
    ("comfort", 8132),
    ("villa", 8133),
    ("para14a", 8134),
]

MONITOR_MINUTES = 5


def run_cmd(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def compose_cmd(house_name: str, port: int, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOUSE_NAME": house_name, "HOUSE_PORT": str(port)}
    cmd = ["docker", "compose", "-p", f"sim-{house_name}", "-f", COMPOSE_FILE, *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def wait_healthy(container: str, timeout: int = 120) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = run_cmd(["docker", "inspect", "--format", "{{.State.Health.Status}}", container], timeout=10)
        status = result.stdout.strip()
        if status == "healthy":
            return True
        if status == "unhealthy":
            return False
        time.sleep(3)
    return False


def install_hemm(container: str) -> bool:
    run_cmd(
        [
            "docker",
            "exec",
            container,
            "pip",
            "install",
            "--quiet",
            "/hemm-src",
            "--trusted-host",
            "pypi.org",
            "--trusted-host",
            "files.pythonhosted.org",
        ],
        timeout=180,
    )
    # Check if hemm is installed despite warnings
    check = run_cmd(["docker", "exec", container, "pip", "show", "hemm"], timeout=10)
    return "hemm" in check.stdout


def check_hemm_errors(container: str) -> list[str]:
    """Get hemm-related ERROR lines from container logs (not warnings)."""
    result = run_cmd(["docker", "logs", container, "--tail", "100"], timeout=10)
    all_output = result.stdout + result.stderr
    errors = []
    for line in all_output.splitlines():
        if "ERROR" in line and ("hemm" in line.lower() or "custom_components/hemm" in line):
            errors.append(line.strip())
    return errors


def count_all_errors(container: str) -> int:
    """Count total ERROR lines in container logs."""
    result = run_cmd(["docker", "logs", container, "--tail", "500"], timeout=10)
    all_output = result.stdout + result.stderr
    count = 0
    for line in all_output.splitlines():
        if "ERROR" in line:
            # Exclude known harmless errors
            if "homeassistant_alerts" in line:
                continue
            if "CERTIFICATE_VERIFY_FAILED" in line:
                continue
            count += 1
    return count


def teardown_house(name: str, port: int) -> None:
    _LOG.info("Tearing down %s...", name)
    # Remove container by name first (may be from different compose project)
    container = f"hemm-sim-{name}"
    run_cmd(["docker", "rm", "-f", container], timeout=30)
    # Then tear down any compose project resources
    compose_cmd(name, port, "down", "-v", "--remove-orphans", timeout=60)
    token_file = BIN_DIR / f".ha_sim_token_{name}"
    if token_file.exists():
        token_file.unlink()


def main():
    results: dict[str, dict] = {}
    setup_script = str(Path(__file__).parent / "_setup_house.py")

    for house_name, port in HOUSES:
        _LOG.info("=" * 60)
        _LOG.info("HOUSE: %s (port %d)", house_name, port)
        _LOG.info("=" * 60)

        container = f"hemm-sim-{house_name}"
        result_entry = {
            "started": False,
            "hemm_installed": False,
            "setup_done": False,
            "stable_5min": False,
            "errors": [],
        }

        try:
            # Cleanup any previous state
            teardown_house(house_name, port)

            # Start container
            _LOG.info("[%s] Starting container...", house_name)
            r = compose_cmd(house_name, port, "up", "-d")
            if "Started" not in r.stderr and "Started" not in r.stdout and "Running" not in r.stderr:
                _LOG.error("[%s] Failed to start: %s", house_name, r.stderr[:500])
                results[house_name] = result_entry
                continue

            # Wait for healthy
            _LOG.info("[%s] Waiting for HA healthy...", house_name)
            if not wait_healthy(container, timeout=120):
                _LOG.error("[%s] HA did not become healthy", house_name)
                results[house_name] = result_entry
                continue
            result_entry["started"] = True
            _LOG.info("[%s] HA healthy", house_name)

            # Install hemm
            _LOG.info("[%s] Installing hemm...", house_name)
            if not install_hemm(container):
                _LOG.error("[%s] Failed to install hemm", house_name)
                results[house_name] = result_entry
                continue
            result_entry["hemm_installed"] = True
            _LOG.info("[%s] hemm installed", house_name)

            # Restart to load hemm
            _LOG.info("[%s] Restarting HA...", house_name)
            run_cmd(["docker", "restart", container], timeout=60)
            time.sleep(5)
            if not wait_healthy(container, timeout=120):
                _LOG.error("[%s] HA not healthy after restart", house_name)
                results[house_name] = result_entry
                continue
            _LOG.info("[%s] HA healthy with hemm", house_name)

            # Run setup script
            _LOG.info("[%s] Running setup...", house_name)
            r = subprocess.run(
                [sys.executable, setup_script, house_name],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(Path(__file__).parent.parent.parent),
            )
            if r.returncode != 0:
                # Check if setup actually succeeded despite returncode (PS stderr issue)
                if "setup complete" in r.stderr or "setup complete" in r.stdout:
                    _LOG.info("[%s] Setup completed (stderr noise)", house_name)
                    result_entry["setup_done"] = True
                else:
                    _LOG.error(
                        "[%s] Setup failed:\nstdout: %s\nstderr: %s", house_name, r.stdout[-500:], r.stderr[-500:]
                    )
                    results[house_name] = result_entry
                    continue
            else:
                result_entry["setup_done"] = True
            _LOG.info("[%s] Setup complete", house_name)

            # Monitor for 5 minutes
            _LOG.info("[%s] Monitoring for %d minutes...", house_name, MONITOR_MINUTES)
            start_time = time.monotonic()
            check_interval = 30  # seconds
            stable = True

            while time.monotonic() - start_time < MONITOR_MINUTES * 60:
                elapsed = int(time.monotonic() - start_time)
                time.sleep(check_interval)

                # Check container is still running
                r = run_cmd(["docker", "inspect", "--format", "{{.State.Status}}", container], timeout=10)
                if r.stdout.strip() != "running":
                    _LOG.error("[%s] Container stopped at %ds!", house_name, elapsed)
                    stable = False
                    break

                # Check health
                r = run_cmd(["docker", "inspect", "--format", "{{.State.Health.Status}}", container], timeout=10)
                if r.stdout.strip() != "healthy":
                    _LOG.error("[%s] HA unhealthy at %ds!", house_name, elapsed)
                    stable = False
                    break

                # Check for hemm errors
                hemm_errors = check_hemm_errors(container)
                if hemm_errors:
                    _LOG.warning("[%s] HEMM errors at %ds: %s", house_name, elapsed, hemm_errors[:3])
                    result_entry["errors"].extend(hemm_errors[:3])

                error_count = count_all_errors(container)
                _LOG.info(
                    "[%s] %ds/%ds — healthy, %d non-trivial errors",
                    house_name,
                    elapsed,
                    MONITOR_MINUTES * 60,
                    error_count,
                )

            if stable:
                result_entry["stable_5min"] = True
                _LOG.info("[%s] STABLE for %d minutes!", house_name, MONITOR_MINUTES)
            else:
                _LOG.error("[%s] UNSTABLE", house_name)

        except Exception as e:
            _LOG.error("[%s] Exception: %s", house_name, e)
            result_entry["errors"].append(str(e))

        finally:
            # Tear down
            teardown_house(house_name, port)

        results[house_name] = result_entry

    # Summary
    _LOG.info("=" * 60)
    _LOG.info("SUMMARY")
    _LOG.info("=" * 60)
    all_pass = True
    for name, r in results.items():
        status = "PASS" if r["stable_5min"] else "FAIL"
        if not r["stable_5min"]:
            all_pass = False
        _LOG.info(
            "  %s: %s (started=%s, hemm=%s, setup=%s, stable=%s, errors=%d)",
            name,
            status,
            r["started"],
            r["hemm_installed"],
            r["setup_done"],
            r["stable_5min"],
            len(r["errors"]),
        )

    if all_pass:
        _LOG.info("ALL 5 HOUSES PASSED!")
    else:
        _LOG.error("SOME HOUSES FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
