# ha-hemm — Home Assistant Integration for HEMM

[![CI](https://github.com/swifty99/ha-hemm/actions/workflows/ci.yml/badge.svg)](https://github.com/swifty99/ha-hemm/actions/workflows/ci.yml)
[![CodeQL](https://github.com/swifty99/ha-hemm/actions/workflows/codeql.yml/badge.svg)](https://github.com/swifty99/ha-hemm/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/swifty99/ha-hemm)](https://github.com/swifty99/ha-hemm/releases/latest)
[![License](https://img.shields.io/github/license/swifty99/ha-hemm)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue)](https://hacs.xyz/)

> **Beta.** The integration is functional and tested, but service names, sensor names, and the manifest schema may still change before 1.0. Contributions and code reviews are welcome — this is a good time to shape the design.

Home Assistant integration for the [HEMM](https://github.com/swifty99/hemm) energy optimization library. HEMM reads device manifests, active constraints, and price/solar forecasts, then produces 24-hour power plans as standard HA sensors. Actuation happens through HA scripts you write; vendor quirks stay in your automations.

## Key Design Points

- Solver outputs are `sensor.hemm_*` entities. Actuation is via HA scripts you write. No custom frontend.
- Every device must declare a `safe_default` script. HEMM calls it if the coordinator crashes or times out. It is the answer to "what happens if HEMM dies at 3 AM?"
- Every service accepts `dry_run: true`. The solver runs and fires events, but nothing is actuated.
- Constraints carry numeric `priority_penalty` values. When two constraints compete for the same power budget, the higher number wins. The resolution is logged and inspectable.
- Zero vendor-specific code in the HEMM core. Vendor quirks (defrost cycles, legionella prevention, utility lockout windows) belong in HA automations, not in the energy manager.

## Why HEMM

Rule-based energy management ("charge EV when solar > 3 kW") works for a single device. With multiple competing consumers — EV, heat pump, battery, hot water — rules produce conflicts that require manual trade-offs. HEMM replaces per-device rules with a single MILP solver that considers all devices simultaneously. Each device declares its constraints and cost function in a JSON manifest; the solver finds the globally optimal schedule and re-runs every 15 minutes.

The central design decision is that the energy manager contains zero vendor-specific code. All hardware quirks stay in HA automations, where they can be shared across the community without changes to the core. A comparison with EMHASS and manual approaches is in the [onboarding guide](docs/onboarding.md#comparison-with-alternatives) (LLM-generated; not independently reviewed — treat as indicative).

## Zeitdynamik — The Sonnenproblem

Not every device is a battery that HEMM can schedule hours ahead. Real homes have three device classes:

| Class | Behavior | Example |
|-------|----------|---------|
| **planned** | Solver schedules 15-min slots ahead | Battery, EV charger |
| **reactive** | Follows a setpoint in real-time (seconds) | Heat pump modulation, inverter curtailment |
| **passive** | Only monitored, never actuated | Non-smart appliances, grid meter |

Each device declares its `control_class` in the config flow. HEMM creates a **reason sensor** (`sensor.hemm_<device>_reason`) explaining *why* the current setpoint was chosen: `pv_surplus`, `cheap_grid`, `constraint`, `idle`, `manual`, or `safety_default`.

Three reference blueprints ship for the three classes:
- `hemm_passive_meter` — energy-sensor mapping for passive devices
- `hemm_reactive_follower` — seconds-interval loop reading setpoints
- `hemm_planned_watchdog` — drift detection → `hemm.replan` with `device_filter`

The `hemm.replan` service accepts an optional `device_filter` list to re-optimize only specific devices without disturbing the rest of the fleet.

## Onboarding

→ **[Onboarding Guide](docs/onboarding.md)** — principles, two worked examples (4-device setup → full 7-device house), what HA objects to create, troubleshooting.

## Installation

### HACS (coming soon)

HACS support is in progress. When available, add `https://github.com/swifty99/ha-hemm` as a custom repository in HACS, install "HEMM Energy Optimizer", restart Home Assistant, and add the integration via Settings → Integrations → Add → HEMM.

### Manual

1. Download the [latest release](https://github.com/swifty99/ha-hemm/releases/latest) and unzip it.
2. Copy the `custom_components/hemm/` directory into your HA configuration directory at `config/custom_components/hemm/`. You can do this via the [File Editor add-on](https://www.home-assistant.io/integrations/file_editor/), a Samba share, or SFTP — no SSH required.
3. Restart Home Assistant. HA automatically installs the `hemm` Python library on first load (it is declared in the integration's `manifest.json` requirements).
4. Add the integration via Settings → Integrations → Add → HEMM.

## Testing

ha-hemm uses four test layers:

- **Unit tests** run in-process against `pytest-homeassistant-custom-component`. No Docker required, completes in under 30 seconds.
- **Container tests** start a real HA instance in Docker, install the integration, and exercise it via the REST API. CI runs these on every push against three HA versions (stable, previous, beta).
- **Sim house tests** provision complete houses (2–9 devices each) in Docker and verify 5-minute stability. Five house variants cover all 7 device types, all 3 control classes, all 7 constraint types, and real-world quirks (defrost lockout, legionella, EV plug lifecycle, §14a grid reduction).
- **Pi tests** (planned) will validate performance on Raspberry Pi hardware under realistic resource constraints.

See [docs/testing.md](docs/testing.md) for how to run each layer locally.

## Contributing

Issues, pull requests, and code reviews are welcome. The project is in early-access beta, so architectural feedback is particularly useful at this stage. Please open an issue before large changes to avoid duplicated effort.

Solver logic, constraint vocabulary, and manifest schema changes belong in the [HEMM core library](https://github.com/swifty99/hemm).

## Development

This integration is developed alongside the HEMM core library. Both repos live under one parent directory:

```
~/dev/hemm/
├── hemm/       # core library (PyPI package)
└── ha-hemm/    # this repo (HA custom component)
```

```bash
uv venv
uv pip install -e ".[dev]"
uv pip install -e ../hemm  # editable install of core

make test   # unit tests
make ci     # lint + test
```

## License

MIT
