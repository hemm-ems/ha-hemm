# ha-hemm — Testing Guide

This document explains how ha-hemm is tested, what the tests actually verify, how you can run them yourself, and where we know the coverage is thin. It is written for someone new to the project — or to software testing in general — so it tries to explain the *why* at each step, not just the *what*.

Testing a Home Assistant custom integration is harder than testing a pure library. The integration's job is to translate between HA's runtime (config flows, entities, coordinators) and the HEMM core library — so testing against mocks would only confirm that the code calls the mock correctly, not that it actually works inside a real HA instance. This is why ha-hemm's test suite goes to some lengths to run against a real, live HA instance for the tests that matter most.

---

## The Three Layers

ha-hemm's tests are organized into three layers, each with a different scope and a different cost.

**Unit tests** are the fastest and cheapest. They cover config flows, device flows, coordinator lifecycle, diagnostics, sensor creation, and identification stubs — all running in-process against a simulated HA runtime provided by `pytest-homeassistant-custom-component`. No Docker, no network, no hactl. They run in under 10 seconds and serve as a quick sanity check during development.

**Container tests** are the main event. They start a real Home Assistant instance in a Docker container, install the companion addon inside it, run hactl commands against the running HA, and check the output. These tests are slower (roughly two minutes once Docker images are cached, longer on first pull), but they are the ones that tell us whether the integration actually works in a real HA installation. Every interaction with HA goes through the real [hactl](https://github.com/swifty99/hactl) CLI binary — the same tool a human would use — rather than a purpose-built Python test client.

**Pi tests** cover hardware validation on a Raspberry Pi running HA OS. They verify that HEMM performs acceptably under realistic resource constraints (ARM CPU, limited RAM, SD card I/O). These are not automated in CI; they run manually against a physical Pi instance using hactl pointed at the Pi's IP. This layer is planned for Phase 8.

Each layer has its own pytest marker, and each layer is enforced independently in CI. You can think of the layers as a pyramid: many small unit tests at the base, a broad set of container tests in the middle, and a focused hardware validation at the top.

---

## Layer 1: Unit Tests

Unit tests live in `tests/` (top-level, not under `integration/`). They use `pytest-homeassistant-custom-component` to spin up a minimal in-process HA runtime — no Docker, no real network. To run them:

```bash
# Via Makefile (Linux/macOS/WSL)
make test

# Direct (Windows or any platform)
uv run pytest
```

This takes roughly 9 seconds and requires nothing beyond Python and the dev dependencies.

What the unit tests cover:

| Test file | What it checks |
|---|---|
| `test_smoke.py` | Domain constant is `"hemm"`, manifest.json valid, constants consistency |
| `test_config_flow.py` | Config flow creation with defaults, duplicate detection, solver backend selection |
| `test_device_flow.py` | All 7 device types (Room, ThermostatLoad, HeatPump, WaterHeater, Battery, PVForecast, EVCharger) in beginner & pro tiers, `safe_default` validation, schema builder correctness |
| `test_options_flow.py` | Settings management and device addition via options flows |
| `test_init.py` | Integration setup, coordinator creation, platform forwarding, unload entry |
| `test_sensor.py` | Sensor entity creation (empty result without sub-entries) |
| `test_diagnostics.py` | Diagnostics structure, `tested_ha_version` presence |
| `test_identification.py` | All 7 device identifier stubs return `None`, registry completeness |
| `test_markers.py` | Test marker demonstration (`unit`, `container`, `pi`, `slow`) |

45 unit tests exist across these files. The default `uv run pytest` invocation runs only unit tests — container, pi, and slow tests are excluded by the `addopts` setting in `pyproject.toml`.

### Windows-specific considerations

On Windows, `pytest-homeassistant-custom-component` interacts with `pytest-socket` (which blocks network access by default). The integration's `conftest.py` handles this automatically:

- `pytest_configure` enables sockets on Windows (ProactorEventLoop is incompatible with socket blocking)
- The `auto_enable_custom_integrations` fixture is set to allow loading the hemm integration

No manual configuration is needed — `uv run pytest` works on Windows, Linux, and macOS.

---

## Layer 2: Container Tests

Container tests live in `tests/integration/` and carry the `@pytest.mark.container` marker. The marker is what keeps them out of a plain `uv run pytest` invocation — you have to opt in explicitly. This is a deliberate design choice: running container tests requires Docker, and many development workflows (editing, linting, quick feedback loops) should not be blocked on Docker availability.

To run the full container test suite:

```bash
# Via Makefile — full cycle: install hactl, start stack, test, teardown
make test-container

# Or for fast iteration (start stack once, run tests repeatedly)
make docker-up                    # Start stack (once)
make test-container-quick         # Run tests (fast, stack stays up)
make docker-down                  # Tear down when done

# Direct commands (Windows — no make required)
docker compose -f docker-compose.test.yml up -d
# ... wait for healthy, install hemm, restart HA (see below) ...
$env:SKIP_DOCKER_COMPOSE="1"
uv run pytest tests/integration/ -m container --tb=short -v -o "addopts="
```

The first run takes roughly 3–4 minutes because Docker has to pull the Home Assistant image (~1.5 GB) and the companion image. Subsequent runs take about 2 minutes because images are cached locally.

### How the container stack works

The test stack is defined in `docker-compose.test.yml` and consists of a single HA container with the companion running inside it:

```
┌──────────────────────────────────────────────────────┐
│  hemm-ha-test container                              │
│                                                      │
│  ┌──────────────────────────┐  ┌──────────────────┐  │
│  │ HA Core (stable)         │  │ hactl-companion   │  │
│  │ Port: 8123 (→ host)      │  │ Port: 9100 (→ h) │  │
│  └──────────────────────────┘  └──────────────────┘  │
│                                                      │
│  Volumes:                                            │
│  - ha-config:/config                                 │
│  - custom_components/hemm (bind, ro)                 │
│  - ../hemm (bind, ro at /hemm-src)                   │
│  - configuration.yaml (bind, ro)                     │
│                                                      │
│  pip install: hemm core + hactl_companion            │
│  Companion started as background process             │
│  SUPERVISOR_TOKEN=integration-test-token-12345       │
└──────────────────────────────────────────────────────┘
         ▲
         │ REST/WS API (8123) + Companion API (9100)
         │
    ┌────┴──────────────────────────┐
    │  Test runner (pytest on host) │
    │  └─ hactl binary (subprocess) │
    │     └─ --dir <tmpdir>/.env    │
    │        HA_URL + HA_TOKEN +    │
    │        COMPANION_URL          │
    └───────────────────────────────┘
```

**Home Assistant container** (`hemm-ha-test`): Runs the real HA Core image. The hemm custom component is bind-mounted read-only at `/config/custom_components/hemm`. The hemm core library source is mounted at `/hemm-src` and pip-installed into the container (hemm is not yet on PyPI, so HA can't install it from `manifest.json` requirements automatically). A `configuration.yaml` fixture is also bind-mounted.

**Companion** (inside the HA container): The [hactl-companion](https://github.com/swifty99/hactl_companion) is pip-installed into the HA container and started as a background process. It provides YAML filesystem access that HA's REST/WS API doesn't expose (reading `configuration.yaml`, listing config files, template evaluation with filesystem context). It runs on port 9100, exposed to the host. Auth uses a static `SUPERVISOR_TOKEN` set via environment variable.

**hactl binary**: The real [hactl](https://github.com/swifty99/hactl) Go CLI binary, downloaded from GitHub releases to `.bin/hactl`. All test assertions go through this binary via subprocess calls — the same commands a human developer would use. The hactl `.env` includes `COMPANION_URL=http://127.0.0.1:9100` so hactl auto-discovers the companion.

### Container lifecycle (what `make docker-up` does)

1. `docker compose up -d --wait` starts the HA container and waits for it to be healthy
2. `pip install /hemm-src` inside the container installs the hemm core library
3. `pip install git+https://github.com/swifty99/hactl_companion.git` installs the companion
4. `docker restart hemm-ha-test` restarts HA so it picks up the hemm package
5. `docker compose up -d --wait` waits for HA healthy again
6. Companion started as background process (`SUPERVISOR_TOKEN=... python3 -m companion`)
7. Poll `http://127.0.0.1:9100/v1/health` until companion is ready

### Onboarding (handled by pytest fixtures)

When tests start, the `ha_token` session-scoped fixture:

1. Waits for HA to be ready (polling `/api/`)
2. Checks if HA still needs onboarding (via `/api/onboarding`)
3. If onboarding needed, runs headless onboarding using stdlib `urllib`:
   - `POST /api/onboarding/users` — creates owner account
   - `POST /auth/token` — exchanges auth code for access token
   - `POST /api/onboarding/core_config` and `/api/onboarding/analytics` — completes onboarding steps
   - WebSocket `auth/long_lived_access_token` — creates a long-lived token
4. Caches token to `.bin/.ha_test_token` for reuse with `SKIP_DOCKER_COMPOSE=1`
5. Creates a temp directory with `.env` file (`HA_URL` + `HA_TOKEN` + `COMPANION_URL`) for hactl
6. Returns the `Hactl` wrapper object that all tests use

### What the container tests cover

Every test file exercises a distinct area of the integration through the real hactl CLI:

| Test file | Tests | What it checks |
|---|---|---|
| `test_container.py` | 14 | Core integration lifecycle: HA healthy, hactl version, config flow setup, integration loaded, entities visible, reload works, no error logs, add all 7 device types via options flow |
| `test_hactl_companion.py` | 8 | Companion features via hactl: template evaluation (simple + states + invalid), script listing, automation listing, service calls |
| `test_hactl_config.py` | 13 | Config flow lifecycle: flow start returns form, data schema present, flow inspect, flow creates entry, abort on duplicate, config entries listing, entry data validation, options flow start/add device/add battery/safe_default required, reload keeps entry, config check passes |
| `test_hactl_entities.py` | 11 | Entity discovery: domain sensor listing, hemm pattern matching, all entities accessible, per-device sensors (battery 3 sensors, EV charger sensors), entity show/full/naming/history/related/anomalies (6 skipped — see Honest Gaps) |
| `test_hactl_health.py` | 9 | System health: HA running, version reporting, hactl binary version, custom component visibility via `cc ls` and `cc logs`, error log summary, component log filter, no unresolved hemm issues |
| `test_hactl_manifest.py` | 10 | Manifest validation: device config produces valid manifest for all 7 types (via config flow round-trip), diagnostics contains devices, invalid device rejected, manifest schema enforced |
| `test_hactl_stress.py` | 9 | Stress testing: 3 rapid reloads, no error logs after reloads, add 3 devices sequentially, entry remains loaded after many devices, dashboard CRUD (ls/create/show), entity state stability, health stability, no errors at end |

80 container tests collected. 74 pass. 6 are skipped (see Honest Gaps below).

### The Hactl wrapper

`tests/integration/hactl.py` provides the `Hactl` class — a typed Python wrapper around hactl subprocess calls. Each method maps to a hactl command:

```python
hactl = Hactl(binary=".bin/hactl", instance_dir="/tmp/ha-test-xyz")
result = hactl.health()                                 # hactl health --json
result = hactl.ent_ls(domain="sensor", pattern="hemm")  # hactl ent ls --domain sensor --pattern hemm --json
result = hactl.config_flow_start("hemm")                # hactl config flow-start hemm --json
```

All methods return `HactlOutput` with `.success`, `.stdout`, `.stderr`, and `.json_data`. Tests assert against the parsed JSON.

**One exception**: `config_entries()` queries the HA REST API directly (via urllib) because hactl does not yet have a `config entries` command. This is tracked as [hactl issue #1](https://github.com/swifty99/hactl/issues) in [ISSUES_HACTL_COMPANION.md](ISSUES_HACTL_COMPANION.md).

### Fixtures

The container test fixture is a single `configuration.yaml` at `tests/integration/config/configuration.yaml`. It is mounted into the HA container and provides a minimal but functional HA configuration.

Unlike hactl's multi-fixture approach (basic/faulty/realistic), ha-hemm currently uses a single fixture. The integration creates its own state through config flows during tests — each test class typically sets up a fresh HEMM config entry, adds devices, and verifies the result.

### Companion addon

The companion runs inside the HA container as a pip-installed Python package. It gives hactl filesystem access to the HA config directory. In tests, it is used for:

- Template evaluation (`hactl tpl eval '{{ states("sensor.x") }}'`)
- Listing scripts and automations
- Service calls via hactl

The companion is installed and started automatically during container setup. If it fails to start, companion-dependent tests skip gracefully (they do not fail).

---

## Layer 2.5: Sim House Tests

Sim house tests sit between container tests and Pi tests. They exercise the full device provisioning lifecycle against realistic house configurations — each house has a distinct mix of device types, control classes, constraints, and quirks modeled as HA automations.

While container tests verify that individual features work in isolation, sim tests verify that a complete house with multiple devices coexists without errors for an extended period. Each house runs in its own Docker container with a house-specific HA configuration overlay.

### The five houses

| House | Port | Devices | Quirks |
|---|---|---|---|
| **starter** | 8130 | PV + Battery (2) | None — baseline |
| **family** | 8131 | PV + Battery + EV (3) | EV plug lifecycle, priority conflicts |
| **comfort** | 8132 | PV + Battery + HP + Room + WH + Thermostat (6) | HP defrost lockout, legionella cycle, safe-default watchdog |
| **villa** | 8133 | All 7 types, 9 devices incl. pool + passive kitchen | All control classes, all constraint types |
| **para14a** | 8134 | PV + Battery + HP + Room + EV (5) | §14a grid reduction (simultaneous HP+EV lockout) |

Each house is defined declaratively in `tests/sim/houses/<name>/house.yaml` with an accompanying `automations.yaml` for quirk-specific HA automations. Adding a new house variant means creating a new directory with those two files — no Python changes required.

### House YAML structure

```yaml
name: starter
description: "PV + Battery baseline — simplest HEMM house"
ha_port: 8130

hub:
  solver_backend: milp_central
  horizon_hours: 24
  max_iterations: 50
  price_adapter: template

devices:
  - type: battery
    tier: beginner
    config:
      device_name: "House Battery"
      capacity_kwh: 10.0
      max_charge_kw: 5.0
      max_discharge_kw: 5.0
    safe_default_script: script.hemm_battery_safe

constraints:
  - window_id: battery_morning_soc
    device_name: "House Battery"
    deadline: "07:00"
    requirement:
      type: min_soc_until
      min_soc_pct: 50
    priority_penalty: 2.0

quirks: []
```

### Running sim tests

```bash
# Run all 5 houses sequentially (each gets its own container lifecycle)
make sim-test

# Or run the full orchestrator with 5-minute stability monitoring
uv run python tests/sim/_run_all_houses.py

# Single house (manual lifecycle)
make sim-up HOUSE=starter        # Start container
make sim-setup HOUSE=starter     # Onboard + provision devices
make sim-down HOUSE=starter      # Tear down

# Full single-house lifecycle
make sim-all HOUSE=starter

# Check running sim containers
make sim-status
```

### How the sim stack works

```
tests/sim/
├── docker-compose.sim.yml          # Parameterized compose (HOUSE_NAME, HOUSE_PORT)
├── base_config/
│   ├── configuration.yaml          # Shared HA config (mock sensors, input entities)
│   └── scripts.yaml                # No-op safe_default scripts for all device types
├── houses/
│   ├── starter/
│   │   ├── house.yaml              # Declarative house definition
│   │   └── automations.yaml        # House-specific HA automations
│   ├── family/
│   ├── comfort/
│   ├── villa/
│   └── para14a/
├── runner.py                       # Setup engine (reads YAML, drives hactl)
├── conftest.py                     # Pytest fixtures (sim_house parametrized fixture)
├── test_sim_houses.py              # 7 tests × 5 houses = 35 parametrized tests
├── _setup_house.py                 # Standalone setup script (used by Makefile)
└── _run_all_houses.py              # Full orchestrator with stability monitoring
```

Each container gets:
- The HEMM custom component (bind-mounted read-only)
- The hemm core library (pip-installed after container start)
- Base HA config with mock price sensor (template), input_booleans for quirk triggers (EV plug, HP defrost, grid reduction), and input_numbers for simulated device states
- House-specific automations overlaid from `houses/<name>/automations.yaml`

The setup engine (`runner.py` / `_setup_house.py`) drives the full provisioning:

1. Wait for HA healthy
2. Headless onboarding (create owner → auth code → access token → WS long-lived token)
3. Create HEMM hub via config flow
4. For each device: 3-step options flow (add_device → select_device → configure_device)
5. Verify entities exist and hub is loaded

### Adding a new house

1. Create `tests/sim/houses/<name>/house.yaml` following the structure above
2. Create `tests/sim/houses/<name>/automations.yaml` (can be `[]` for no quirks)
3. Add port mapping to `_setup_house.py` `HOUSE_PORTS` dict and `_run_all_houses.py` `HOUSES` list
4. Add named volume to `docker-compose.sim.yml`
5. Run `make sim-all HOUSE=<name>` to verify

The test file auto-discovers houses via `discover_house_names()` — pytest parametrization picks up new houses automatically.

### What the sim tests check

| Test | What it verifies |
|---|---|
| `test_house_setup_succeeds` | All devices provision without error |
| `test_hub_is_loaded` | HEMM config entry stays in `loaded` state |
| `test_entities_created` | At least N hemm entities exist (N = device count) |
| `test_replan_service_callable` | `hemm.replan` service runs without error |
| `test_no_hemm_errors_in_log` | No ERROR-level hemm log entries |
| `test_constraint_count_matches` | Constraint definitions are non-empty |
| `test_device_count_matches` | Config entry exists and is loaded |

Pi tests carry the `@pytest.mark.pi` marker and are planned for Phase 8. They will run hactl from the development machine pointed at a real HA instance on a Raspberry Pi:

```bash
# Point hactl at the Pi
mkdir -p ~/ha/pi
cat > ~/ha/pi/.env << 'EOF'
HA_URL=http://<pi-ip>:8123
HA_TOKEN=<pi-long-lived-token>
EOF

# Verify connectivity
hactl --dir ~/ha/pi health

# Run Pi tests
HA_BASE_URL=http://<pi-ip>:8123 HA_TOKEN=<token> SKIP_DOCKER_COMPOSE=1 uv run pytest -m pi
```

The same hactl binary and test patterns work — it's just pointed at a different HA instance via `--dir`. Pi tests will focus on:

- Solver performance under ARM CPU constraints (both backends)
- Memory usage during optimization runs
- SD card I/O impact on coordinator updates
- Long-soak stability (48–72h continuous operation)

---

## Running Tests Locally

The only hard prerequisite for unit tests is Python 3.12+ and `uv`. Container tests additionally require Docker.

| Goal | Command | Docker needed | Approximate time |
|---|---|---|---|
| Quick sanity check | `uv run pytest` | No | ~9 seconds |
| Lint + unit tests | `make ci` (or run both manually) | No | ~15 seconds |
| Container tests (full cycle) | `make test-container` | Yes | ~3 min first, ~2 min cached |
| Container tests (stack running) | `make test-container-quick` | Yes (running) | ~2 min |
| Sim house tests (all 5) | `make sim-test` | Yes | ~35 min |
| Sim single house | `make sim-all HOUSE=starter` | Yes | ~7 min |
| Specific test file | see below | Yes (running) | ~20 seconds |
| All CI checks | `make ci-full` | Yes | ~3 min |
| Lint only | `make lint` | No | ~2 seconds |
| Format | `make format` | No | ~2 seconds |

To run a specific container test file with the stack already running:

```bash
SKIP_DOCKER_COMPOSE=1 uv run pytest tests/integration/test_hactl_health.py -m container -v -o "addopts="
```

**A common mistake on Windows**: `make` is not available in PowerShell by default. Either install Make (`winget install GnuWin32.Make` or via Chocolatey), use WSL, or run the underlying commands directly:

```powershell
# Equivalent of "make test"
uv run pytest

# Equivalent of "make ci"
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/
uv run pytest

# Equivalent of "make test-container" (manual steps on Windows)
docker compose -f docker-compose.test.yml up -d --wait
# Wait for HA healthy...
docker exec hemm-ha-test pip install /hemm-src
docker exec hemm-ha-test pip install git+https://github.com/swifty99/hactl_companion.git
docker restart hemm-ha-test
docker compose -f docker-compose.test.yml up -d --wait
# Start companion inside HA container
docker exec -d hemm-ha-test sh -c 'SUPERVISOR_TOKEN=integration-test-token-12345 python3 -m companion'
# Wait for companion healthy...
$env:SKIP_DOCKER_COMPOSE="1"
uv run pytest tests/integration/ -m container --tb=short -v -o "addopts="
docker compose -f docker-compose.test.yml down -v --remove-orphans
```

### hactl binary management

The `hactl_binary` pytest fixture automatically downloads the latest hactl release if not found:

1. Checks `.bin/hactl` (project-local, from `make install-hactl`)
2. Checks PATH (system-wide install)
3. Downloads latest release from `https://github.com/swifty99/hactl/releases/latest/download/`

To install manually:

```bash
# Via Makefile (recommended)
make install-hactl

# Via go install (if Go is installed)
go install github.com/swifty99/hactl/cmd/hactl@latest

# Manual download (see Makefile install-hactl target for platform-specific commands)
```

### WSL2 gotchas (Windows development)

**Docker context**: WSL2 uses Docker Engine inside the Linux VM. Ensure you're using the WSL2 context, not Docker Desktop's Windows context:
```bash
docker context ls  # Should show "default" → unix:///var/run/docker.sock
```

**Port forwarding**: Docker containers inside WSL2 expose ports to `localhost` on Windows automatically. `http://localhost:8123` works from both Windows and WSL. If running Docker in a separate WSL distro, you may need `127.0.0.1` or the WSL IP.

**Volume mounts**: Named Docker volumes work identically. Bind mounts must use Linux paths in WSL. Always run tests from within the WSL filesystem (`~/repos/...`), not from `/mnt/c/...` which is slower and can cause permission issues.

**ProactorEventLoop**: If running Python tests directly on Windows (not WSL), `pytest-socket` requires special handling — already done in `conftest.py` (socket enable).

### Iterating on code changes

After changes to **custom component code** (`custom_components/hemm/`):
- Volume is mounted read-only; HA reads at startup
- Restart HA: `docker restart hemm-ha-test`, wait for healthy
- Re-run tests: `make test-container-quick`

After changes to **hemm core** (`../hemm/src/`):
- Reinstall in container: `docker exec hemm-ha-test pip install /hemm-src`
- Restart HA: `docker restart hemm-ha-test`, wait for healthy
- Re-run tests

---

## CI/CD Enforcement

The test suite only works as a quality gate if it runs automatically on every change. ha-hemm uses GitHub Actions for this. The workflow is defined in `.github/workflows/ci.yml` and runs on every push to `main` and every pull request targeting `main`.

The pipeline has three jobs:

**Lint** runs `ruff check` and `ruff format --check` on all source files. A formatting or style failure blocks merge.

**Unit Tests** runs `uv run pytest` against a Python version matrix (3.12, 3.13). This installs the hemm core library from GitHub (`git+https://github.com/swifty99/hemm.git`) since it's not on PyPI yet. Each matrix entry runs independently.

**Container Tests** runs the full container test suite against a matrix of HA versions. This starts the Docker Compose stack, pre-installs hemm into the container, and runs `uv run pytest tests/integration/ -m container`. The HA version matrix includes:

- Current stable version (required — failure blocks merge)
- Previous stable version (required — failure blocks merge)
- Next/beta version (non-blocking — failure shows as a warning)

If a future HA release breaks the integration, the container tests will fail before users encounter the issue. The non-blocking beta run gives advance notice of upcoming HA changes without making every PR depend on the stability of a pre-release build.

**Concurrency**: The workflow uses a concurrency group keyed on the branch/PR, so pushing new commits to the same PR cancels the previous run. This prevents wasted CI minutes.

**Dependabot** (`.github/dependabot.yml`) opens pull requests weekly for pip dependency updates and GitHub Actions version bumps.

---

## What Is Covered

The table below summarizes the current coverage across ha-hemm's features. "Unit" means there are in-process HA tests; "Container" means the feature is exercised by hactl against a real HA instance; "Companion" means the companion addon is involved.

| Feature area | Unit | Container | Sim |
|---|---|---|---|
| Config flow (hub setup) | ✓ | ✓ | ✓ |
| Config flow (duplicate detection) | ✓ | ✓ | — |
| Options flow (settings) | ✓ | ✓ | — |
| Options flow (add device) | ✓ | ✓ | ✓ |
| Device flow (all 7 types, beginner) | ✓ | ✓ | ✓ |
| Device flow (pro tier) | ✓ | — | — |
| `safe_default` validation | ✓ | ✓ | — |
| Integration setup/unload | ✓ | ✓ | — |
| Integration reload | ✓ | ✓ | — |
| Coordinator creation | ✓ | ✓ | — |
| Sensor entity creation | ✓ | ✓ | ✓ |
| Per-device sensor count | — | ✓ | ✓ |
| Diagnostics endpoint | ✓ | ✓ | — |
| Manifest validation (all 7 types) | — | ✓ | — |
| Identification stubs | ✓ | — | — |
| Repair flow framework | ✓ | — | — |
| HA health check | — | ✓ | ✓ |
| Error log monitoring | — | ✓ | ✓ |
| Custom component visibility | — | ✓ | — |
| Issues/repairs listing | — | ✓ | — |
| Stress (rapid reloads) | — | ✓ | — |
| Stress (multi-device add) | — | ✓ | ✓ |
| Dashboard CRUD | — | ✓ | — |
| Template evaluation (via hactl) | — | ✓ | — |
| Script listing (via hactl) | — | ✓ | — |
| Automation listing (via hactl) | — | ✓ | — |
| Config check (service call) | — | ✓ | — |
| Multi-device coexistence (2–9 devices) | — | — | ✓ |
| Control class mixing (passive+reactive+planned) | — | — | ✓ |
| All 7 constraint types | — | — | ✓ |
| Quirk automations (defrost, legionella, §14a) | — | — | ✓ |
| 5-minute stability under load | — | — | ✓ |
| `hemm.replan` after full provisioning | — | — | ✓ |

---

## Honest Gaps

No test suite is complete, and this one is no exception. The following areas are not well covered, and we think it is worth being explicit about them.

**Entity state values**: 6 container tests are skipped because the coordinator currently returns stub data (`0.0` power, `0%` confidence, `"idle"` mode). These tests (`test_ent_show_hemm_entity`, `test_ent_show_full_attributes`, `test_all_hemm_entities_have_prefix`, `test_ent_hist_returns_data`, `test_ent_related_works`, `test_ent_anomalies_clean_after_setup`) will be enabled in Phase 6 when the coordinator produces real optimization results. The skips are controlled by checking whether any hemm entity has a non-unknown state.

**Actuator layer**: The actuator-call engine with verification contracts (Phase 7) is not implemented yet, so there are no tests for script execution, timeout handling, retry logic, or the `safe_default` watchdog.

**Live optimization**: The coordinator currently returns stub data. Real MILP/distributed solver integration (Phase 6) will add tests that verify actual plan values, price signal responses, and constraint satisfaction.

**Online identification**: The identification framework exists but all identifiers return `None`. When Phase 6 activates identification, tests will verify parameter refinement, confidence reporting, and the repair-issue notification flow.

**Cross-platform CI**: All CI jobs run on Ubuntu. The integration works on Windows (verified locally), but platform-specific issues (path separators, event loop behavior) would not be caught in CI until a user reports them.

**Multi-entry scenarios**: Tests currently set up a single HEMM config entry. Multiple entries (e.g., separate buildings) are not tested.

**HA version edge cases**: The container test matrix covers 2–3 HA versions. Breaking changes in HA's config flow API, entity registry, or coordinator patterns between major versions could slip through if they happen outside the tested range.

**Companion YAML write operations**: Only read operations and security boundaries are tested. Write operations (creating scripts, automations) will be tested when the actuator layer (Phase 7) needs them.

---

## Known Upstream Issues

Issues discovered during testing are tracked in [ISSUES_HACTL_COMPANION.md](ISSUES_HACTL_COMPANION.md). Key items:

**hactl**:
- No `config entries` command — workaround: direct REST API call in test wrapper
- `--options` flag undocumented for `config flow-step`
- `cc ls` doesn't detect mounted custom_components (uses companion filesystem, not HA registry)
- `config flow-start` hangs on integration load failure instead of failing fast

**hactl-companion**:
- Alpine resolves `localhost` → `::1` but companion binds IPv4 only (use `127.0.0.1`)
- `resolve=true` returns `"null\n..."` for valid YAML
- No logging output (silent startup)

These are documented workarounds, not blockers. All container tests pass with the current workarounds in place.

---

## Quick Reference

```bash
# Prerequisites
docker info                                 # Docker must be running (container tests only)
uv --version                                # uv must be installed

# Unit tests
uv run pytest                               # Unit tests only (~9s, no Docker)

# Lint
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/

# Container tests
make test-container                         # Full cycle: hactl install → start stack → test → teardown
make test-container-quick                   # Tests only (stack must be running)
make docker-up                              # Start stack manually
make docker-down                            # Tear down stack

# Manual hactl usage (after onboarding / token cached)
.bin/hactl --dir <tmpdir> health
.bin/hactl --dir <tmpdir> ent ls --domain sensor --pattern hemm
.bin/hactl --dir <tmpdir> config flow-start hemm

# CI checks
make ci                                     # lint + unit tests
make ci-full                                # lint + unit tests + container tests
```

The CI pipeline enforces all of the above on every pull request. If the CI badge at the top of the README is green, all required checks have passed against the current `main` branch.
