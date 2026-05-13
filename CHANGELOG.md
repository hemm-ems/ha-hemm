# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
- `test_hactl_companion.py` reduced to hactl-routed tests only (templates, scripts, automations, services). Direct companion API tests (health, config files, security) moved to the companion repo.
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
