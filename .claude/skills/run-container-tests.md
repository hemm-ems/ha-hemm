---
name: run-container-tests
description: Run container-based integration tests (requires Docker)
command: make test-container
---

Runs `make test-container` which executes `uv run pytest -m container`. Docker must be running. Use for HA container-based testing.
