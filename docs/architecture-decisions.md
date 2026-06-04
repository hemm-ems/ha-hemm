# Architecture Decisions & Learnings

Decisions made during implementation that deviate from or refine the original plan. Grouped by topic.

## Modeling & Schema

- **Pydantic v2 instead of TypedDicts** — runtime validation, JSON Schema export, discriminated unions, serialization all in one. Better fit for LLM-writable declarative manifests.
- **Plan-change penalty is L1 (linear) not quadratic** — HiGHS (LP/MILP) doesn't support quadratic objectives. Absolute-value penalty via auxiliary variables achieves the same anti-short-cycling effect.
- **Thermal constraints (ReachMinTempOnce, HoldTempBand) are enforced via the `node` primitive** — a room/tank compiles to a thermal node with RC dynamics (U-value, thermal mass) and the constraint binds on that node's temperature state. Solar gain is still simplified. A thermal constraint aimed at a device with no thermal node is now rejected at validation, not silently ignored.
- **Consumer models are greedy/heuristic, not sub-problem MILP** — sort slots by effective cost, allocate cheaply. Sufficient for distributed protocol convergence. Full sub-problem solvers can swap in later.

## HA Integration

- **No ConfigSubentryFlow** — HA 2024.12 doesn't expose it. Options flow mixin pattern (`HemmDeviceFlowMixin` into `HemmOptionsFlow`) with action chooser step instead.
- **Devices stored as list in `config_entry.data["devices"]`** — each with UUID, device_type, tier, plus type-specific fields.
- **`pytest-homeassistant-custom-component` used** — provides proper HA test harness (`hass` fixture, `MockConfigEntry`, `enable_custom_integrations`).
- **`OptionsFlowWithConfigEntry` → `OptionsFlow`** — former was deprecated. `OptionsFlow` auto-receives `self.config_entry`.
- **Mode is a sensor (read-only) not a switch** — pending service layer for control.
- **`homeassistant` removed from direct dev deps** — `pytest-homeassistant-custom-component` pulls it transitively.

## Import Shadowing

- **`custom_components/hemm/` shadows installed `hemm` core package** — both on sys.path, `custom_components` first. Solution: deferred imports + `_HEMM_CORE_AVAILABLE` flag at module level.
- **`_async_update_data()` must never run the solver** — returns cached results or idle stubs. Solver execution during `async_config_entry_first_refresh()` causes setup timeout (`CancelledError` is `BaseException`, not caught by `except Exception`). Solver runs happen via services or scheduled tasks only.

## Testing Infrastructure

- **hactl binary via subprocess** — `Hactl` class wraps `subprocess.run()` with `--dir` and `--json`. Real binary, not Python shim. Tests = executable documentation.
- **Python `HactlClient` retained only for onboarding** — hactl doesn't do headless onboarding via WebSocket.
- **`pytest-socket` blocks real network on Linux CI** — resolved via `@pytest.mark.enable_socket` on integration tests. On Windows, socket blocking disabled entirely (`ProactorEventLoop` needs real sockets).
- **`aiohttp.ThreadedResolver()` required on Windows** — `aiodns` incompatible with `ProactorEventLoop` (HA's `HassEventLoopPolicy`).
- **aiohttp `ClientSession` must be function-scoped** — session-scoped fails with timeout context manager errors across asyncio tasks. Split: session-scoped `ha_token`, function-scoped `ha_client`.
- **Docker healthcheck uses `/api/onboarding`** — `/api/` requires auth (401). `/api/onboarding` is unauthenticated and available once HTTP server is up.

## Build & CI

- **`uv` used everywhere** — never pip/venv directly.
- **hatchling build config** — `packages = ["custom_components/hemm"]` because hatch can't auto-detect the layout.
- **`hemm` core not on PyPI** — CI runs `docker exec hemm-ha-test pip install git+...` after container start. `manifest.json` declares the requirement.
- **TCH/TC001/TC003 ruff rules disabled** — Pydantic and HA framework require runtime imports for type annotations.
- **`TESTED_HA_VERSION` is a static constant** — build-time injection deferred to Phase 9 release workflow.

## Solver

- **Pyomo `appsi_highs` interface** — direct APPSI to HiGHS. No separate solver executable needed.
- **`pyyaml` is transitive via pyomo** — not explicitly in dependencies. `types-PyYAML` added for mypy.
- **SimRunner is solver-agnostic** — accepts `Any` solver (duck-typed) to run both backends through same infrastructure.
- **Room consumer returns zero power** — rooms are thermal zones, not actuators. Influence is indirect.
