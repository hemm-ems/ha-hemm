# AGENT.md

Norms for everyone working on HEMM — human or model. Same file applies to `hemm` (core) and `ha-hemm` (integration); content is intentionally identical so the rules don't drift between repos.

## Repo layout

HEMM lives in two repos under one parent directory:

```
~/dev/hemm/
├── hemm/              # core (this repo or its sibling)
└── ha-hemm/           # HA integration (this repo or its sibling)
```

Claude Code is run on the parent directory `~/dev/hemm/`. Cross-repo edits in one session.

During development the integration imports the core via editable install:

```bash
cd ha-hemm
uv pip install -e ../hemm
```

For releases, `manifest.json` in `ha-hemm` pins a PyPI version of `hemm`.

## Tests

The canonical test commands are Make targets. Don't guess, don't run pytest directly:

| Command | When to use |
|---------|-------------|
| `make test` | Default. Unit tests only. Runs on every save during development. |
| `make ci` | What the lint+test CI minimally does. Run before pushing. |
| `make test-container` | Container-based integration tests. Docker must be running. |
| `make ci-full` | `ci` + `test-container`. Run before opening a PR. |
| `make test-slow` | Long-run sims. Mostly nightly CI or manual. |
| `make test-pi` | On Pi hardware only. Manual or self-hosted runner. |

`make test` MUST be fast (< 30s on a dev laptop). If it gets slow, something is wrongly tagged. Tests have markers (`unit`, `container`, `pi`, `slow`) and the default run excludes everything but `unit`. Don't bypass that.

After a code change: run linter, fix, then test. `make ci` does both in order.

## Working principles

**Plan before acting.** No change without a plan. Draft, review, then implement. The implementation plan and the concept doc are the contract — read them before proposing changes.

**Read before writing.** Read the relevant code, tests, and existing manifests first. No assumptions about code you haven't seen.

**Done = green tests.** A feature without tests is unfinished. A milestone without passing tests on the relevant tier is not done. Acceptance criteria in the implementation plan are non-negotiable.

**No speculative fixes.** If a test fails, understand why before touching code. Read the error, trace the cause, fix the root — not the symptom.

**Write-path always dry-run capable.** Any action that changes state in the real world (actuator calls, constraint modifications) must support a dry-run mode for testing and debugging.

## Make targets reference

- `make test` — fast unit tests (default pytest run)
- `make ci` — lint + test
- `make ci-full` — ci + container tests
- `make test-container` — container tests only
- `make test-slow` — long-running sims
- `make test-pi` — Pi hardware tests
- `make lint` — ruff check + format check
- `make format` — auto-format
- `make build` — package for HACS
