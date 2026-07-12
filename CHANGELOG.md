# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [2026.7.4] - 2026-07-12

### Fixed

- **Horizon anchored to the current slot, not midnight (003:RW1 live-verify follow-up).** A live tariff series begins at 00:00 today, and the central MILP aligns prices to slots positionally and stamps the plan from the first price timestamp. The coordinator passed the raw series, so every solve was anchored at midnight — it planned the already-elapsed part of the day, applied the **measured** SoC/temperature (`initial_state`) at 00:00 instead of now, ended the 24 h horizon at 00:00 tomorrow (only a few hours ahead of an afternoon solve), and made the plan sensor report the midnight slot as the "current" setpoint. The coordinator now drops elapsed slots so the horizon starts at the slot containing `now`; found during the RW1 live verify on a real home.

## [2026.7.3] - 2026-07-12

### Added

- **RW1 — the coordinator reads the real home (003:RW1).** Every solve now starts from measured state instead of synthetic defaults:
  - **Live price (FR-101/102):** a hub **price entity** is read each tick and passed to the price adapter as a pre-fetched series (Nordpool/Tibber/EPEX attribute shapes supported). **The silent flat-price fallback is removed** — an unreadable price source (or an adapter that yields nothing) now raises a `price_unavailable` repair and **skips the solve** rather than optimize on a fake flat price; it clears automatically on recovery.
  - **Real PV (FR-103):** a PV device's **forecast entity** (Forecast.Solar `watts` dict / Solcast list) is resampled to per-slot kW and overlaid via `generation_forecast`.
  - **Measured state + economics (FR-104/105):** battery **SoC entity** (% → kWh) and optional room/tank **temperature entities** seed the solver's `initial_state`; the MILP is built with a hub **feed-in tariff** and outdoor temperature from an optional **weather entity**.
- **`services.yaml`** for all ten services (`replan`, `simulate`, `set_price_curve`, `set_solver`, `add_constraint_window`, `remove_constraint`, `bump_priority`, `tick`, `force_watchdog`, `actuate_now`) — fixes the `Failed to load services.yaml` startup error and gives every service field a UI selector.

### Changed

- **Core pinned to `hemm==2026.7.2`** — brings the solver `initial_state` API and Backend B weather-driven COP that RW1 relies on.
- Plan sensors expose a per-slot `schedule` attribute and use bare-role names (`Plan`/`Confidence`/`Mode`/`Reason`), composed with the device name (fixes the previous name duplication).
- EV plug/charge-state selector accepts a `sensor` (e.g. go-e car-status), not just `binary_sensor`; the options flow gains a **remove-device** step.

### Fixed

- The `add_constraint_window` service translation used `requirement_value`; renamed to the real schema key `requirement_params` so the localized field attaches.

## [2026.7.2] - 2026-07-11

### Fixed

- **HACS install ("could not download").** The release packaging was never HACS-installable:
  - `hacs.json` used `filename: "hemm-ha-{{ version }}.zip"`, but current HACS does not template the `filename` field — it matches the release asset name literally, so it requested a nonexistent `hemm-ha-{{ version }}.zip` and reported "could not download". The filename is now the static `hemm-ha.zip`.
  - The release zip wrapped the integration in a `hemm/` folder, but HACS extracts the zip's contents into `config/custom_components/<domain>/`, which would have nested it to `custom_components/hemm/hemm/`. The zip is now built from inside `custom_components/hemm/` so its files sit at the zip root. `release.yml` and `make build` are aligned.

### Added

- **`pool_pump` device type** — a controllable electrical load registered in the config flow and manifest builder; proves the core's primitive component model end-to-end (a new device plans through a real HA replan with zero solver code). Now also registered in the identification stub registry.
- **Primitive metadata in the manifest schema** surfaced to the integration (`x-hemm-primitives`), with an integration test asserting existing manifests validate unchanged.

### Changed

- **Core pinned to `hemm==2026.7.1`** — brings the primitive component model (spec 003), storage round-trip losses, PV generation in the energy balance (FR-006), grid-settlement fix (FR-002), and scenario-trust features (FR-011/012/013).
- Solver construction and the pyomo import moved off the event loop; solves are serialized on a process-wide lock. An explicit `hemm.replan` now waits for an in-flight solve and then runs fresh instead of silently returning the cached result. This also fixes a native deadlock when a config-entry reload left two coordinators solving concurrently.
- Updated `docs/solver-decision.md`: after the core's primitive-component refactor (spec 003), the distributed backend now clears the A/B gate (~1.2% average cost gap, 6/6 scenarios converge, was ~96% / 1-of-6). Central MILP remains the default; the historical 2026-05-11 record is preserved.

### Fixed

- Example automations now call `hemm.add_constraint_window` with the schema-correct `device_id` (4 of 8 examples previously used an unsupported `device_filter` key and failed validation when pasted verbatim).
- Repair fix-flows dispatch on the issue type: `actuation_verify_failed` and `actuation_self_confirming` issues no longer open the generic "solver degraded" flow.
- `translations/en.json` and `de.json` synced to full key parity with `strings.json` (`control_class`, actuation issues, services block).
- `make build` derives the version from `manifest.json` and produces the HACS-conform `hemm-ha-<version>.zip`.

## [2026.5.2] - 2026-05-29

### Added

- **Example Automations** (replaces blueprints):
  - 8 example automations in `custom_components/hemm/examples/`: ev_plug_schedule, hp_defrost_lockout, legionella_protection, para14a_grid_reduction, dry_run_verification, reactive_follower, planned_watchdog, passive_meter
  - Standard HA automation format (id, alias, trigger, action) — no blueprint parameterization
  - Users adapt examples directly or let an LLM generate tailored automations from them
- **hactl CRUD for automations** (requires hactl v2026.5.3+):
  - `auto_create` / `auto_delete` methods in hactl wrapper
  - Sim houses now create automations dynamically via `hactl auto create --confirm`
  - No more volume-mounted `automations.yaml` in Docker — automations are registry-based

### Removed

- `custom_components/hemm/blueprints/` directory (6 blueprint YAML files)
- Volume-mounted automations in sim house Docker setup

### Changed

- Manifest pin now targets `hemm==2026.5.2`, and the integration `version` field matches that release.
- **Zeitdynamik-Erweiterung (Sonnenproblem)**:
  - `control_class` field in device configuration (`passive` / `reactive` / `planned`, default: `planned`)
  - `sensor.hemm_<device>_reason` — per-device reason sensor (enum: pv_surplus, cheap_grid, constraint, idle, manual, safety_default)
  - 4 sensors per device (was 3): plan, confidence, mode, **reason**
  - `device_filter` parameter on `hemm.replan` service for selective re-optimization
  - Container integration tests for all Zeitdynamik features
- **Sim House Testing Framework**:
  - 5 declarative house variants (starter, family, comfort, villa, para14a) each provisioned in Docker
  - YAML-driven house definitions — add new house variants without Python changes
  - Covers all 7 device types, all 3 control classes, all 7 constraint types
  - Real-world quirk automations: HP defrost lockout, legionella cycle, EV plug lifecycle, §14a grid reduction
  - `make sim-up/sim-setup/sim-down/sim-all/sim-test` lifecycle targets
  - 40 parametrized pytest tests (8 checks × 5 houses)
- **Time-warp auto mode (PI controller):** `WARP_SPEED` can now be left empty for
  adaptive speed — a background PI controller reads cgroup v2 CPU stats and
  adjusts the warp multiplier to hit a configurable CPU target (default 50%).
  Speed starts at 1× and ramps to the host's ceiling (up to 1000× on fast hardware).
  New env knobs: `WARP_CPU_TARGET`, `WARP_KP`, `WARP_KI`, `WARP_SLEW`,
  `WARP_PI_INTERVAL`, `WARP_SPEED_MIN`, `WARP_SPEED_MAX`.
- **Speed file sharing:** PI controller writes current speed to `/tmp/.warp_speed`
  so `docker exec` processes inherit the main process's speed instead of starting at 1×.
- **Villa stress config:** 14-sensor, 5-automation simulated home
  (`tests/warp/config/villa.yaml`) for scheduler stress testing under warp.
- **Warp stress tests:** 8-test suite (`tests/warp/test_warp_stress.py`) measuring
  peak/sustained speed, scheduler tick rate, clock monotonicity, concurrent exec stability.
- **CI warp gate:** GitHub Actions `warp-test` job runs smoke tests in both auto and
  fixed (100×) modes on every push/PR.

## [2026.5.0] - 2026-05-11

### Added

- **Onboarding guide** (`docs/onboarding.md`): principles, two worked examples (simple + full house), quick-start, comparison table, troubleshooting
- **README rewrite**: community-facing pitch with key differentiators
- **CI/CD overhaul**: CodeQL security scanning, auto-release (monthly), patch-release (on demand), hardened dependabot auto-merge, SECURITY.md, HACS manifest, README badges
- **HA-style versioning**: vYYYY.M.PATCH convention (matching HA ecosystem)

- **Phase 6 — Live Optimization:**
  - 8 HA services: `replan`, `simulate`, `set_price_curve`, `set_solver`, `add_constraint_window`, `remove_constraint`, `bump_priority`, `tick` — all support `dry_run` parameter
  - 5 HA events: `hemm_plan_updated`, `hemm_solver_switched`, `hemm_constraint_added`, `hemm_constraint_resolved`, `hemm_identification_complete`
  - 7 constraint types: `reach_min_temp_once`, `hold_temp_band`, `min_soc_until`, `min_energy_until`, `forbidden_window`, `min_runtime_per_day`, `max_runtime_per_day`
  - Sensor entities: 3 sensors per device (plan/confidence/mode)
  - A/B solver comparison framework + `solver-decision.md` documentation
  - 3 example automation blueprints: legionella protection, EV plugin schedule, dry-run verification
  - Solver switching at runtime (MILP ↔ distributed) via service call
  - Constraint lifecycle management with TTL/expiry
  - Device identification stubs (7 device types)
  - Extended diagnostics: constraint state, solver backend, lambda count, dry-run log
  - Repair flow: `solver_degraded` issue when core unavailable

- **Testing — 97 unit tests + container integration suite:**
  - `test_services.py`: 52 tests covering all 8 services, all 7 constraint types, nasty type combos (zero/huge penalty, negative flex, negative prices, rapid add/remove), event firing, sensors, diagnostics, repairs, identification
  - `test_hactl_services.py`: 22 container tests for dry-run, solver switching, price curves, constraints, onboarding E2E
  - All datetime usage audited to `dt_util.utcnow()` (HA convention)
  - All identifiers hemm-prefixed, events use `{DOMAIN}_` prefix

### Changed

- **Architecture: companion inside HA container** — the hactl-companion now runs as a pip-installed background process inside the HA container instead of a separate Docker container. This matches the real HA addon architecture where the companion has direct filesystem access to `/config`.
- Removed `hactl_client.py` — onboarding is now handled inline in `conftest.py` using stdlib `urllib` + `aiohttp` WebSocket (no separate client class).
- `docker-compose.test.yml` simplified to a single service (no companion container, no shared network).
- Companion-focused hactl tests reduced to hactl-routed coverage only (templates, scripts, automations, services). Direct companion API tests (health, config files, security) moved to the companion repo.
- CI workflow updated: companion installed and started inside HA container.
- `testing.md` updated with new single-container architecture diagram.

## [0.2.0] - 2026-05-06

### Added

- **Config flow step 1:** Hub setup with name, horizon, max iterations, price adapter, solver backend
- **Options flow:** Adjustable runtime parameters (horizon, iterations, price source, solver)
- **DataUpdateCoordinator:** Stub with 15-min update interval, solver/adapter configuration
- **Diagnostics endpoint:** Shows `tested_ha_version`, config entry, coordinator state
- **Repair-issue framework:** `solver_degraded` repair flow example
- **Translations:** English and German (`en.json`, `de.json`) for config/options/issues
- **In-process HA tests:** 19 tests using `pytest-homeassistant-custom-component`
  - Config flow tests (5): form display, entry creation, defaults, duplicate abort, distributed solver
  - Options flow tests (2): form display, option updates
  - Init/coordinator tests (5): setup, coordinator creation, properties, data, unload
  - Diagnostics tests (2): content structure, tested_ha_version presence
  - Smoke tests (4): domain constant, manifest fields, HACS structure, constants consistency
- **Container test setup:** Docker compose file, hactl REST client, integration test fixtures
- **CI matrix:** Python 3.12 + 3.13, lint + test jobs separated
- Integration coverage: 87%

### Changed

- Config flow now collects full hub configuration (was name-only)
- `__init__.py` uses DataUpdateCoordinator pattern with update listener
- Replaced `homeassistant` dev dependency with `pytest-homeassistant-custom-component`
- TCH ruff rules removed (HA needs runtime imports like Pydantic does)

## [0.1.0] - 2026-05-06

### Added

- Initial integration skeleton with domain `hemm`
- Config flow (single instance)
- Pytest configuration with markers
- Makefile with canonical targets
- GitHub Actions CI
