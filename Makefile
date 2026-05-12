.PHONY: test test-container test-pi test-slow ci ci-full lint format install-hactl docker-up docker-down docker-reset sim-up sim-setup sim-down sim-all sim-status sim-test

## Default: fast unit tests only
test:
	uv run pytest

## Container-based integration tests (Docker required)
## Starts fresh stack, installs hemm, runs tests, tears down
test-container: install-hactl docker-up
	@echo "Running container integration tests..."
	uv run pytest -m container --tb=short -q
	@$(MAKE) docker-down

## Container tests against already-running stack (faster iteration)
test-container-quick: install-hactl
	SKIP_DOCKER_COMPOSE=1 uv run pytest -m container --tb=short -q

## Pi hardware tests (manual / self-hosted runner)
test-pi:
	uv run pytest -m pi

## Long-running simulation tests
test-slow:
	uv run pytest -m slow

## CI minimum: lint + unit tests
ci: lint test

## CI full: ci + container tests
ci-full: ci test-container

## Lint and format check
lint:
	uv run ruff check custom_components/ tests/
	uv run ruff format --check custom_components/ tests/

## Auto-format
format:
	uv run ruff format custom_components/ tests/
	uv run ruff check --fix custom_components/ tests/

## --- Docker Stack Management ---

## Start HA + companion containers, install hemm, restart HA
docker-up:
	@echo "Starting HA + companion stack..."
	docker compose -f docker-compose.test.yml up -d
	@echo "Waiting for HA to be healthy..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' hemm-ha-test 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA healthy'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' hemm-ha-test 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA healthy"
endif
	@echo "Installing hemm package in container..."
	docker exec hemm-ha-test pip install /hemm-src 2>&1 | tail -1
	@echo "Restarting HA to load hemm..."
	docker restart hemm-ha-test
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' hemm-ha-test 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA ready with hemm'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' hemm-ha-test 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA ready with hemm"
endif
	@echo "Waiting for companion..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' hemm-companion-test 2>$$null } while ($$s -eq 'starting'); Write-Host \"companion: $$s\""
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' hemm-companion-test 2>/dev/null)" = "starting" ]; do sleep 2; done; echo "companion: $$(docker inspect --format '{{.State.Health.Status}}' hemm-companion-test)"
endif
	@echo "Stack ready!"

## Stop and remove containers + volumes
docker-down:
	docker compose -f docker-compose.test.yml down -v --remove-orphans
ifeq ($(OS),Windows_NT)
	@if exist .bin\.ha_test_token del .bin\.ha_test_token
else
	@rm -f .bin/.ha_test_token
endif

## Full reset: down + fresh up
docker-reset: docker-down docker-up

## Show stack status
docker-status:
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" --filter name=hemm

## Show HA logs (last 20 lines)
docker-logs:
	docker logs hemm-ha-test --tail 20

## Show companion logs
docker-logs-companion:
	docker logs hemm-companion-test --tail 20

## Install hactl binary (downloads latest release from GitHub)
install-hactl:
ifeq ($(OS),Windows_NT)
	@if not exist .bin mkdir .bin
	@powershell -Command "$$ProgressPreference='SilentlyContinue'; $$tag=(Invoke-RestMethod 'https://api.github.com/repos/swifty99/hactl/releases/latest').tag_name; $$v=$$tag.TrimStart('v'); $$url=\"https://github.com/swifty99/hactl/releases/download/$$tag/hactl_$${v}_windows_amd64.zip\"; Invoke-WebRequest -Uri $$url -OutFile '.bin/hactl.zip'; Expand-Archive -Path '.bin/hactl.zip' -DestinationPath '.bin' -Force; Remove-Item '.bin/hactl.zip'"
	@echo "hactl installed to .bin/hactl.exe"
else
	@mkdir -p .bin
	@TAG=$$(curl -sL https://api.github.com/repos/swifty99/hactl/releases/latest | grep tag_name | head -1 | cut -d'"' -f4); \
	 VERSION=$${TAG#v}; \
	 ARCH=$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'); \
	 OS_NAME=$$(uname -s | tr '[:upper:]' '[:lower:]'); \
	 curl -sL "https://github.com/swifty99/hactl/releases/download/$${TAG}/hactl_$${VERSION}_$${OS_NAME}_$${ARCH}.tar.gz" | tar xz -C .bin/
	@chmod +x .bin/hactl
	@echo "hactl installed to .bin/hactl"
endif

## --- Sim House Management ---
## Usage: make sim-up HOUSE=starter
##        make sim-all HOUSE=starter
##        make sim-test   (runs all houses sequentially via pytest)

HOUSE ?= starter
SIM_COMPOSE = tests/sim/docker-compose.sim.yml

# Map house names to ports
_sim_port_starter = 8130
_sim_port_family  = 8131
_sim_port_comfort = 8132
_sim_port_villa   = 8133
_sim_port_para14a = 8134

SIM_PORT = $(_sim_port_$(HOUSE))
SIM_CONTAINER = hemm-sim-$(HOUSE)

## Start a sim house container
sim-up:
	@echo "Starting sim house: $(HOUSE) (port $(SIM_PORT))..."
	HOUSE_NAME=$(HOUSE) HOUSE_PORT=$(SIM_PORT) docker compose -f $(SIM_COMPOSE) up -d --wait
	@echo "Waiting for HA to be healthy..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA healthy'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA healthy"
endif
	@echo "Installing hemm package in $(SIM_CONTAINER)..."
	docker exec $(SIM_CONTAINER) pip install --quiet /hemm-src 2>&1 | tail -1
	@echo "Restarting HA to load hemm..."
	docker restart $(SIM_CONTAINER)
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA ready with hemm'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA ready with hemm"
endif
	@echo "Sim house $(HOUSE) ready at http://localhost:$(SIM_PORT)"

## Setup a sim house (onboard + provision devices) — container must be running
sim-setup: install-hactl
	@echo "Setting up sim house: $(HOUSE)..."
	uv run python -c "from tests.sim.runner import full_setup, HouseConfig; from pathlib import Path; h = HouseConfig.from_yaml(Path('tests/sim/houses/$(HOUSE)/house.yaml')); full_setup(h, 'http://localhost:$(SIM_PORT)', Path('.bin/hactl') if not '$(OS)' == 'Windows_NT' else Path('.bin/hactl.exe'), Path('.bin'))"
	@echo "Sim house $(HOUSE) setup complete!"

## Stop and remove a sim house container
sim-down:
	@echo "Stopping sim house: $(HOUSE)..."
	HOUSE_NAME=$(HOUSE) HOUSE_PORT=$(SIM_PORT) docker compose -f $(SIM_COMPOSE) down -v --remove-orphans
ifeq ($(OS),Windows_NT)
	@if exist .bin\.ha_sim_token_$(HOUSE) del .bin\.ha_sim_token_$(HOUSE)
else
	@rm -f .bin/.ha_sim_token_$(HOUSE)
endif
	@echo "Sim house $(HOUSE) stopped."

## Full lifecycle: up + setup (container + onboard + devices)
sim-all: sim-up sim-setup
	@echo "Sim house $(HOUSE) fully provisioned!"

## Show status of all sim containers
sim-status:
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" --filter name=hemm-sim

## Run sim house tests via pytest (sequential, all houses)
sim-test: install-hactl
	@echo "Running sim house tests..."
	uv run pytest tests/sim/ -m sim --tb=short -q --log-cli-level=INFO

## Build (HACS compatible zip)
build:
	@echo "Build step: package custom_components/hemm for HACS"
	@mkdir -p dist
	@cd custom_components && zip -r ../dist/hemm.zip hemm/
