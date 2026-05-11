# ha-hemm — Home Assistant Integration for HEMM

[![CI](https://github.com/swifty99/ha-hemm/actions/workflows/ci.yml/badge.svg)](https://github.com/swifty99/ha-hemm/actions/workflows/ci.yml)
[![CodeQL](https://github.com/swifty99/ha-hemm/actions/workflows/codeql.yml/badge.svg)](https://github.com/swifty99/ha-hemm/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/swifty99/ha-hemm)](https://github.com/swifty99/ha-hemm/releases/latest)
[![License](https://img.shields.io/github/license/swifty99/ha-hemm)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue)](https://hacs.xyz/)

Optimize when your home devices consume, store, and produce energy — using standard HA automations, scripts, and sensors. No proprietary UI, no cloud, no vendor lock-in.

HEMM takes device manifests (what each device can do), active constraints (what you need right now), and price/solar forecasts, then produces 24-hour power plans exposed as regular HA sensors. You control actuation through your own scripts. Vendor quirks live in your automations, not in the energy manager.

## Key Differentiators

- **HA-native interface** — Solver outputs are `sensor.hemm_*` entities. Actuation via HA scripts. Constraints via `hemm.add_constraint_window` service calls from automations. No custom frontend.
- **Zero vendor code in core** — Heat pump defrost, EV charger quirks, legionella cycles: all handled by HA automations using HEMM's constraint vocabulary. Vendor coverage scales with community automations, not core PRs.
- **Tiered configuration** — Beginner: "35 m², good insulation". Pro: direct U-values, COP curves, thermal mass. Mix tiers per device.
- **Safe defaults enforced** — Every device must have a fallback script. HEMM fails to safe, never to off.
- **Dry-run everything** — Every service accepts `dry_run: true`. Verify before going live.
- **Numeric conflict resolution** — Constraints have `priority_penalty` numbers. Legionella at 10.0 beats comfort at 3.0. Transparent, debuggable.

## Getting Started

→ **[Onboarding Guide](docs/onboarding.md)** — Principles, two worked examples (simple 4-device setup → full 7-device house), what HA objects to create, troubleshooting.

## Quick Install

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "HEMM Energy Optimizer"
3. Restart Home Assistant
4. Add the integration via Settings → Integrations → Add → HEMM

### Manual

Copy `custom_components/hemm/` to your HA `config/custom_components/` directory.

## Development

This integration is developed alongside the HEMM core library. Both repos live under one parent directory:

```
~/dev/hemm/
├── hemm/       # core library (PyPI package)
└── ha-hemm/    # this repo (HA custom component)
```

### Setup

```bash
uv venv
uv pip install -e ".[dev]"
uv pip install -e ../hemm  # editable install of core

make test   # run unit tests
make ci     # lint + test
```

### Testing

Three test layers: unit tests (< 30s), container tests (real HA in Docker), Pi hardware tests. See [docs/testing.md](docs/testing.md).

## License

MIT
