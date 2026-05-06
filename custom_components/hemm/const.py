"""Constants for the HEMM integration."""

DOMAIN = "hemm"

# Config keys
CONF_NAME = "name"
CONF_HORIZON_HOURS = "horizon_hours"
CONF_MAX_ITERATIONS = "max_iterations"
CONF_PRICE_ADAPTER = "price_adapter"
CONF_SOLVER_BACKEND = "solver_backend"

# Defaults
DEFAULT_NAME = "HEMM"
DEFAULT_HORIZON_HOURS = 24
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_PRICE_ADAPTER = "template"
DEFAULT_SOLVER_BACKEND = "milp_central"

# Solver backend choices
SOLVER_BACKENDS = ["milp_central", "distributed"]

# Price adapter choices
PRICE_ADAPTERS = ["template", "solcast", "forecast_solar"]

# Tested HA version (set at build time / release)
TESTED_HA_VERSION = "2025.4.0"
