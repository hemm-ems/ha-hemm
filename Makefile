.PHONY: test test-container test-container-quick test-container-sc test-pi test-slow ci ci-full lint format hooks install-hactl docker-up docker-down docker-reset sim-up sim-setup sim-down sim-all sim-status sim-test check-clock branding-audit \
	warp-up warp-down warp-build warp-logs warp-shell warp-set-speed warp-date warp-clock test-warp test-warp-stress

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

## Per-SC run against already-running stack. Usage: make test-container-sc SC=SC-005
## Pairs with `tools/compress_container_log.py` (pipe stderr/stdout into it).
test-container-sc: install-hactl
	@[ -n "$(SC)" ] || (echo "Usage: make test-container-sc SC=SC-005" && exit 2)
	SKIP_DOCKER_COMPOSE=1 uv run pytest -m container -k "$(SC)" --tb=short -q

## Pi hardware tests (manual / self-hosted runner)
test-pi:
	uv run pytest -m pi

## Long-running simulation tests
test-slow:
	uv run pytest -m slow

## CI minimum: lint + clock audit + unit tests
ci: lint check-clock test

## Enable the opt-in pre-push hook (runs `make ci` before every push).
## One-time per clone. Disable with: git config --unset core.hooksPath
hooks:
	git config core.hooksPath .githooks
	@echo "pre-push hook enabled (runs 'make ci'). Skip a push with: git push --no-verify"

## Time-warp audit: forbid direct `dt_util.utcnow`/`datetime.now`/`time.monotonic`
## in the integration. Whitelist: custom_components/hemm/time.py (HAClock).
check-clock:
	uv run python ../hemm/tools/check_clock.py \
		--root custom_components/hemm \
		--allow custom_components/hemm/time.py

## Branding audit: intentionally allowed to fail until the Phase 3 rename lands.
branding-audit:
	python3 ../tools/branding_audit.py

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

## Install hactl binary (pinned release; see AGENT.md tool-pinning norm).
## Must match HACTL_PINNED_VERSION in tests/integration/hactl.py.
## Override for bump testing: make install-hactl HACTL_VERSION=v2026.7.6
HACTL_VERSION ?= v2026.7.5

install-hactl:
ifeq ($(OS),Windows_NT)
	@if not exist .bin mkdir .bin
	@powershell -Command "$$ProgressPreference='SilentlyContinue'; $$tag='$(HACTL_VERSION)'; $$v=$$tag.TrimStart('v'); $$url=\"https://github.com/hemm-ems/hactl/releases/download/$$tag/hactl_$${v}_windows_amd64.zip\"; Invoke-WebRequest -Uri $$url -OutFile '.bin/hactl.zip'; Expand-Archive -Path '.bin/hactl.zip' -DestinationPath '.bin' -Force; Remove-Item '.bin/hactl.zip'"
	@echo "hactl $(HACTL_VERSION) installed to .bin/hactl.exe"
else
	@mkdir -p .bin
	@TAG=$(HACTL_VERSION); \
	 VERSION=$${TAG#v}; \
	 ARCH=$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'); \
	 OS_NAME=$$(uname -s | tr '[:upper:]' '[:lower:]'); \
	 curl -sL "https://github.com/hemm-ems/hactl/releases/download/$${TAG}/hactl_$${VERSION}_$${OS_NAME}_$${ARCH}.tar.gz" | tar xz -C .bin/
	@chmod +x .bin/hactl
	@echo "hactl $(HACTL_VERSION) installed to .bin/hactl"
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
# Must mirror HouseConfig.companion_port (= ha_port + 1000) in tests/sim/runner.py,
# else `make sim-setup` dials a port compose never mapped.
SIM_COMPANION_PORT = $(shell echo $$(( $(SIM_PORT) + 1000 )))
SIM_CONTAINER = hemm-sim-$(HOUSE)
# Must match _COMPANION_TOKEN in tests/sim/runner.py and tests/sim/conftest.py.
SIM_COMPANION_TOKEN = sim-test-token-12345

## Start a sim house container
sim-up:
	@echo "Starting sim house: $(HOUSE) (port $(SIM_PORT))..."
	HOUSE_NAME=$(HOUSE) HOUSE_PORT=$(SIM_PORT) COMPANION_PORT=$(SIM_COMPANION_PORT) docker compose -f $(SIM_COMPOSE) up -d --wait
	@echo "Waiting for HA to be healthy..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA healthy'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA healthy"
endif
	@echo "Installing hemm + companion in $(SIM_CONTAINER)..."
	docker exec $(SIM_CONTAINER) sh -c "touch /config/automations.yaml"
	docker exec $(SIM_CONTAINER) pip install --quiet /hemm-src 2>&1 | tail -1
	docker exec $(SIM_CONTAINER) pip install --quiet "git+https://github.com/hemm-ems/hactl-companion.git@v2026.7.2" 2>&1 | tail -1
	@echo "Restarting HA to load hemm..."
	docker restart $(SIM_CONTAINER)
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA ready with hemm'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' $(SIM_CONTAINER) 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA ready with hemm"
endif
	@echo "Starting companion..."
	docker exec -d $(SIM_CONTAINER) sh -c "SUPERVISOR_TOKEN=$(SIM_COMPANION_TOKEN) python3 -m companion"
	@echo "Sim house $(HOUSE) ready at http://localhost:$(SIM_PORT) (companion :$(SIM_COMPANION_PORT))"

## Setup a sim house (onboard + provision devices) — container must be running
sim-setup: install-hactl
	@echo "Setting up sim house: $(HOUSE)..."
	uv run python -c "from tests.sim.runner import full_setup, HouseConfig; from pathlib import Path; h = HouseConfig.from_yaml(Path('tests/sim/houses/$(HOUSE)/house.yaml')); full_setup(h, 'http://localhost:$(SIM_PORT)', Path('.bin/hactl') if not '$(OS)' == 'Windows_NT' else Path('.bin/hactl.exe'), Path('.bin'))"
	@echo "Sim house $(HOUSE) setup complete!"

## Stop and remove a sim house container
sim-down:
	@echo "Stopping sim house: $(HOUSE)..."
	HOUSE_NAME=$(HOUSE) HOUSE_PORT=$(SIM_PORT) COMPANION_PORT=$(SIM_COMPANION_PORT) docker compose -f $(SIM_COMPOSE) down -v --remove-orphans
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

## --- Time-warp Stack (libwarp LD_PRELOAD in Docker) ---

## Build the warp image (HA + libwarp).
warp-build:
	docker compose -f docker-compose.warp.yml build

## Bring up the warp stack. Override speed with `WARP_SPEED=500 make warp-up`.
## Omit WARP_SPEED for auto mode (PI controller adjusts speed to target CPU).
## NOTE: HEMM core is NOT auto-installed in the warp container — pip's runtime
## use of pthread_cond_timedwait with absolute deadlines doesn't survive the
## virtualized CLOCK_MONOTONIC (waits get stretched proportional to uptime).
## Use the `tests/integration/` stack (docker-compose.test.yml) for full HEMM
## integration tests; the warp stack is for clock/scheduler behavior only.
warp-up: warp-build
	docker compose -f docker-compose.warp.yml up -d
	@echo "Waiting for HA (warp) to be healthy..."
ifeq ($(OS),Windows_NT)
	@powershell -Command "do { Start-Sleep -Milliseconds 2000; $$s = docker inspect --format '{{.State.Health.Status}}' hemm-ha-warp 2>$$null } while ($$s -ne 'healthy'); Write-Host 'HA (warp) healthy'"
else
	@while [ "$$(docker inspect --format '{{.State.Health.Status}}' hemm-ha-warp 2>/dev/null)" != "healthy" ]; do sleep 2; done; echo "HA (warp) healthy"
endif
	@echo "Warp stack ready. Wall vs simulated:"
	@$(MAKE) warp-clock

## Tear down warp stack and volumes.
warp-down:
	docker compose -f docker-compose.warp.yml down -v --remove-orphans

## Tail HA logs (warp).
warp-logs:
	docker logs hemm-ha-warp --tail 30 -f

## Open a shell in the warp container.
warp-shell:
	docker exec -it hemm-ha-warp sh

## Print the current simulated `date` inside the container next to wall-clock.
warp-date:
ifeq ($(OS),Windows_NT)
	@powershell -Command "Write-Host ('wall:      ' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'))"
	@powershell -Command "Write-Host ('container: ' + (docker exec hemm-ha-warp date -u '+%%Y-%%m-%%d %%H:%%M:%%S' 2>$$null).Trim())"
else
	@echo "wall:      $$(date -u +'%Y-%m-%d %H:%M:%S')"
	@echo "container: $$(docker exec hemm-ha-warp date -u +'%Y-%m-%d %H:%M:%S')"
endif

## Print a multi-sample wall vs simulated clock comparison.
warp-clock:
ifeq ($(OS),Windows_NT)
	@powershell -Command "[Threading.Thread]::CurrentThread.CurrentCulture = 'en-US'; 1..5 | ForEach-Object { $$w = [int64][double](Get-Date -UFormat '%%s'); $$c = (docker exec hemm-ha-warp date -u +%%s 2>$$null).Trim(); Write-Host \"wall=$$w  container=$$c  delta=$$([int64]$$c - $$w)s\"; Start-Sleep -Seconds 1 }"
else
	@for i in 1 2 3 4 5; do \
	  W=$$(date -u +%s); \
	  C=$$(docker exec hemm-ha-warp date -u +%s); \
	  echo "wall=$$W  container=$$C  delta=$$((C - W))s"; \
	  sleep 1; \
	done
endif

## Change the warp speed. Requires a stack restart since WARP_SPEED is read once at process start.
## Usage: make warp-set-speed SPEED=500
warp-set-speed:
ifeq ($(OS),Windows_NT)
	@if "$(SPEED)"=="" (echo Usage: make warp-set-speed SPEED=500 && exit /b 2)
else
	@[ -n "$(SPEED)" ] || (echo "Usage: make warp-set-speed SPEED=500" && exit 2)
endif
	@$(MAKE) warp-down >/dev/null
	@WARP_SPEED=$(SPEED) $(MAKE) warp-up

## Run the warp CI gate (smoke test that time advances at configured speed).
test-warp: warp-up
ifeq ($(OS),Windows_NT)
	@powershell -Command "uv run pytest -m warp --tb=short -q; $$rc=$$LASTEXITCODE; $(MAKE) warp-down; exit $$rc"
else
	uv run pytest -m warp --tb=short -q ; rc=$$? ; $(MAKE) warp-down ; exit $$rc
endif

## Run warp stress tests with villa config (local only, ~2 min).
test-warp-stress:
	@WARP_CONFIG=villa.yaml $(MAKE) warp-up
ifeq ($(OS),Windows_NT)
	@powershell -Command "uv run pytest tests/warp/test_warp_stress.py -v -s --tb=short -m warp -o 'addopts='; $$rc=$$LASTEXITCODE; $(MAKE) warp-down; exit $$rc"
else
	uv run pytest tests/warp/test_warp_stress.py -v -s --tb=short -m warp -o 'addopts=' ; rc=$$? ; $(MAKE) warp-down ; exit $$rc
endif

## Build (HACS compatible zip)
## Filename follows hacs.json's "hemm-ha-{{ version }}.zip" pattern, using
## manifest.json's version (the version release.yml tags and publishes).
build:
	@echo "Build step: package custom_components/hemm for HACS"
	@mkdir -p dist
	@VERSION=$$(python3 -c "import json; print(json.load(open('custom_components/hemm/manifest.json'))['version'])") && \
		cd custom_components && zip -r ../dist/hemm-ha-$$VERSION.zip hemm/
