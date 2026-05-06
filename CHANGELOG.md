# Changelog

All notable changes to this project will be documented in this file.

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
