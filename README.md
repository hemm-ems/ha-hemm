# ha-hemm — Home Assistant Integration for HEMM

Custom component for [Home Assistant](https://www.home-assistant.io/) that integrates the [HEMM energy optimizer](https://github.com/hemm-energy/hemm).

## Installation

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
├── hemm/       # core library
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

## License

MIT
