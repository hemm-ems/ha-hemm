"""Hactl subprocess wrapper — drives the real hactl binary for integration tests.

Replaces the purpose-built Python REST client for test assertions.
The real hactl binary is downloaded from GitHub releases and invoked via subprocess.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Default timeout for hactl commands (seconds)
DEFAULT_TIMEOUT = 30

# GitHub release URL pattern
HACTL_RELEASE_URL = "https://github.com/swifty99/hactl/releases/latest/download"


@dataclass
class HactlOutput:
    """Parsed output from a hactl command."""

    returncode: int
    stdout: str
    stderr: str
    json_data: dict[str, Any] | list[Any] | None = None

    @property
    def success(self) -> bool:
        return self.returncode == 0

    @property
    def token_count(self) -> int | None:
        """Extract token count from hactl's [~N tok] header."""
        if self.stdout and self.stdout.startswith("[~"):
            try:
                end = self.stdout.index(" tok]")
                return int(self.stdout[2:end])
            except (ValueError, IndexError):
                pass
        return None


class HactlError(Exception):
    """Raised when hactl command fails."""

    def __init__(self, cmd: list[str], output: HactlOutput) -> None:
        self.cmd = cmd
        self.output = output
        super().__init__(f"hactl failed (rc={output.returncode}): {output.stderr.strip()}")


class Hactl:
    """Subprocess wrapper for the hactl binary.

    Usage:
        hactl = Hactl(binary="/path/to/hactl", instance_dir="/path/to/instance")
        result = hactl.health()
        result = hactl.ent_ls(domain="sensor", pattern="hemm_*")
    """

    def __init__(self, binary: str | Path, instance_dir: str | Path, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._binary = str(binary)
        self._dir = str(instance_dir)
        self._timeout = timeout

    def _run(self, args: list[str], *, json_mode: bool = True, timeout: int | None = None) -> HactlOutput:
        """Run hactl with given arguments."""
        cmd = [self._binary, "--dir", self._dir]
        if json_mode:
            cmd.append("--json")
        cmd.extend(args)

        _LOGGER.debug("Running: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise HactlError(
                cmd, HactlOutput(returncode=-1, stdout="", stderr=f"Timeout after {self._timeout}s")
            ) from e

        output = HactlOutput(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

        if json_mode and result.stdout.strip():
            try:
                # hactl JSON output may have a [~N tok] header line before the JSON
                raw = result.stdout.strip()
                if raw.startswith("[~"):
                    # Skip the token header line
                    lines = raw.split("\n", 1)
                    raw = lines[1] if len(lines) > 1 else ""
                if raw:
                    output.json_data = json.loads(raw)
            except json.JSONDecodeError:
                _LOGGER.debug("Failed to parse JSON from hactl output: %s", result.stdout[:200])

        return output

    def _run_or_raise(self, args: list[str], **kwargs: Any) -> HactlOutput:
        """Run hactl and raise on failure."""
        output = self._run(args, **kwargs)
        if not output.success:
            raise HactlError([self._binary, *args], output)
        return output

    # --- Health & system ---

    def health(self) -> HactlOutput:
        """hactl health — HA version, state, recorder, errors."""
        return self._run_or_raise(["health"])

    def issues(self) -> HactlOutput:
        """hactl issues — active HA repairs/issues."""
        return self._run_or_raise(["issues"])

    def changes(self, since: str = "24h") -> HactlOutput:
        """hactl changes — recent state changes."""
        return self._run_or_raise(["changes", "--since", since])

    # --- Entities ---

    def ent_ls(
        self,
        *,
        domain: str | None = None,
        pattern: str | None = None,
        area: str | None = None,
        label: str | None = None,
    ) -> HactlOutput:
        """hactl ent ls — list entities with optional filters."""
        args = ["ent", "ls"]
        if domain:
            args.extend(["--domain", domain])
        if pattern:
            args.extend(["--pattern", pattern])
        if area:
            args.extend(["--area", area])
        if label:
            args.extend(["--label", label])
        return self._run_or_raise(args)

    def ent_show(self, entity_id: str, *, full: bool = False) -> HactlOutput:
        """hactl ent show — entity state + attributes."""
        args = ["ent", "show", entity_id]
        if full:
            args.append("--full")
        return self._run_or_raise(args)

    def ent_hist(self, entity_id: str, *, since: str = "24h", resample: str | None = None) -> HactlOutput:
        """hactl ent hist — entity history."""
        args = ["ent", "hist", entity_id, "--since", since]
        if resample:
            args.extend(["--resample", resample])
        return self._run_or_raise(args)

    def ent_anomalies(self, entity_id: str) -> HactlOutput:
        """hactl ent anomalies — gap/stuck/spike detection."""
        return self._run_or_raise(["ent", "anomalies", entity_id])

    def ent_related(self, entity_id: str) -> HactlOutput:
        """hactl ent related — automations, siblings, neighbors."""
        return self._run_or_raise(["ent", "related", entity_id])

    # --- Config entries & flows ---

    def config_entries(self) -> HactlOutput:
        """List config entries via HA REST API.

        hactl doesn't have a 'config entries' command, so we query the REST API
        directly using credentials from the instance .env file.
        """
        import urllib.request

        env = self._read_env()
        ha_url = env.get("HA_URL", "http://localhost:8123").replace("localhost", "127.0.0.1")
        ha_token = env.get("HA_TOKEN", "")

        url = f"{ha_url}/api/config/config_entries/entry"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode()
                data = json.loads(raw)
                return HactlOutput(returncode=0, stdout=raw, stderr="", json_data=data)
        except Exception as e:
            return HactlOutput(returncode=1, stdout="", stderr=str(e))

    def _read_env(self) -> dict[str, str]:
        """Read the .env file from the instance directory."""
        env_file = Path(self._dir) / ".env"
        env: dict[str, str] = {}
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
        return env

    def config_flow_start(self, domain: str) -> HactlOutput:
        """hactl config flow-start — start a new config flow."""
        return self._run_or_raise(["config", "flow-start", domain])

    def config_flow_step(self, flow_id: str, data: dict[str, Any], *, options: bool = False) -> HactlOutput:
        """hactl config flow-step — submit data to advance a flow.

        Pass options=True when stepping through an options flow (started via config_options).
        """
        args = ["config", "flow-step", flow_id, "--data", json.dumps(data)]
        if options:
            args.append("--options")
        return self._run_or_raise(args)

    def config_flow_inspect(self, flow_id: str) -> HactlOutput:
        """hactl config flow-inspect — inspect flow state."""
        return self._run_or_raise(["config", "flow-inspect", flow_id])

    def config_options(self, entry_id: str) -> HactlOutput:
        """hactl config options — start options flow for entry."""
        return self._run_or_raise(["config", "options", entry_id])

    def config_check(self) -> HactlOutput:
        """hactl config check — validate HA config."""
        return self._run_or_raise(["svc", "call", "homeassistant.check_config"])

    # --- Automations ---

    def auto_ls(self, *, pattern: str | None = None, label: str | None = None, failing: bool = False) -> HactlOutput:
        """hactl auto ls — list automations."""
        args = ["auto", "ls"]
        if pattern:
            args.extend(["--pattern", pattern])
        if label:
            args.extend(["--label", label])
        if failing:
            args.append("--failing")
        return self._run_or_raise(args)

    def auto_show(self, automation_id: str) -> HactlOutput:
        """hactl auto show — automation config + traces."""
        return self._run_or_raise(["auto", "show", automation_id])

    def auto_create(self, yaml_path: Path | str, *, confirm: bool = True) -> HactlOutput:
        """hactl auto create — create an automation from YAML file."""
        args = ["auto", "create", "-f", str(yaml_path)]
        if confirm:
            args.append("--confirm")
        return self._run_or_raise(args)

    def auto_delete(self, automation_id: str, *, confirm: bool = True) -> HactlOutput:
        """hactl auto delete — delete an automation."""
        args = ["auto", "delete", automation_id]
        if confirm:
            args.append("--confirm")
        return self._run_or_raise(args)

    # --- Scripts ---

    def script_ls(self, *, pattern: str | None = None, label: str | None = None, failing: bool = False) -> HactlOutput:
        """hactl script ls — list scripts."""
        args = ["script", "ls"]
        if pattern:
            args.extend(["--pattern", pattern])
        if label:
            args.extend(["--label", label])
        if failing:
            args.append("--failing")
        return self._run_or_raise(args)

    def script_run(self, script_id: str) -> HactlOutput:
        """hactl script run — execute a script."""
        return self._run_or_raise(["script", "run", script_id])

    # --- Services ---

    def svc_call(self, service: str, data: dict[str, Any] | None = None) -> HactlOutput:
        """hactl svc call — call a service."""
        args = ["svc", "call", service]
        if data:
            args.extend(["-d", json.dumps(data)])
        return self._run_or_raise(args)

    # --- Templates ---

    def tpl_eval(self, template: str) -> HactlOutput:
        """hactl tpl eval — evaluate a Jinja2 template server-side."""
        return self._run_or_raise(["tpl", "eval", template])

    # --- Logs ---

    def log(self, *, errors: bool = False, unique: bool = False, component: str | None = None) -> HactlOutput:
        """hactl log — system log entries."""
        args = ["log"]
        if errors:
            args.append("--errors")
        if unique:
            args.append("--unique")
        if component:
            args.extend(["--component", component])
        return self._run_or_raise(args)

    # --- Custom components ---

    def cc_ls(self) -> HactlOutput:
        """hactl cc ls — installed custom components."""
        return self._run_or_raise(["cc", "ls"])

    def cc_logs(self, component: str, *, unique: bool = False) -> HactlOutput:
        """hactl cc logs — custom component errors."""
        args = ["cc", "logs", component]
        if unique:
            args.append("--unique")
        return self._run_or_raise(args)

    # --- Dashboards ---

    def dash_ls(self) -> HactlOutput:
        """hactl dash ls — list dashboards."""
        return self._run_or_raise(["dash", "ls"])

    def dash_show(self, url_path: str | None = None) -> HactlOutput:
        """hactl dash show — dashboard config."""
        args = ["dash", "show"]
        if url_path:
            args.append(url_path)
        return self._run_or_raise(args)

    def dash_create(self, url_path: str, title: str, *, icon: str = "mdi:home", confirm: bool = False) -> HactlOutput:
        """hactl dash create — create a dashboard."""
        args = ["dash", "create", "--url-path", url_path, "--title", title, "--icon", icon]
        if confirm:
            args.append("--confirm")
        return self._run_or_raise(args)

    # --- Cache ---

    def cache_refresh(self) -> HactlOutput:
        """hactl cache refresh — pull fresh data."""
        return self._run_or_raise(["cache", "refresh"], json_mode=False)

    def version(self) -> HactlOutput:
        """hactl version — version info."""
        return self._run_or_raise(["version"], json_mode=False)


def get_hactl_download_url() -> str:
    """Get the hactl binary download URL for the current platform.

    The release assets use the pattern: hactl_{version}_{os}_{arch}.{ext}
    where ext is .zip on Windows and .tar.gz on Unix.
    We use the GitHub API to resolve the latest version and find the right asset.
    """
    import urllib.request

    # Resolve latest release tag
    api_url = "https://api.github.com/repos/swifty99/hactl/releases/latest"
    req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as resp:
        import json as _json

        data = _json.loads(resp.read())

    tag = data["tag_name"]  # e.g. "v0.5.0"
    version = tag.lstrip("v")  # "0.5.0"

    if sys.platform == "win32":
        asset_name = f"hactl_{version}_windows_amd64.zip"
    elif sys.platform == "darwin":
        import platform

        arch = "arm64" if platform.machine() == "arm64" else "amd64"
        asset_name = f"hactl_{version}_darwin_{arch}.tar.gz"
    else:
        import platform

        arch = "arm64" if platform.machine() == "aarch64" else "amd64"
        asset_name = f"hactl_{version}_linux_{arch}.tar.gz"

    return f"https://github.com/swifty99/hactl/releases/download/{tag}/{asset_name}"


def get_hactl_binary_name() -> str:
    """Get the hactl binary filename for the current platform."""
    if sys.platform == "win32":
        return "hactl.exe"
    return "hactl"
