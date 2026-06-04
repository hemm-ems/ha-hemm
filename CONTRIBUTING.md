# Contributing to ha-hemm

ha-hemm is the Home Assistant integration half of HEMM. It depends on the
pure-Python core:

- **`hemm-ems/ha-hemm`** — this repo (HA custom component, domain `hemm`).
- **`hemm-ems/hemm`** — the core (PyPI `hemm`, import `hemm_core`). CI installs
  it from the core's `main` branch
  (`pip install git+https://github.com/hemm-ems/hemm.git`).

Clone both under one parent directory so the time-warp and branding tools can
find the sibling core checkout at `../hemm`.

## Development Setup

1. Clone both repos under one parent directory
2. Create a virtual environment: `uv venv`
3. Install in dev mode: `uv pip install -e ".[dev]"`
4. Enable the pre-push CI guard: `make hooks` (see below)

## Workflow

1. Create a branch from `main`
2. Make changes
3. **Run `make ci` and wait for green before every push.** This mirrors the
   required `Lint` and `Unit Tests` checks — running it locally is the
   difference between a 10-second fix and a red PR. `make hooks` wires this into
   `git push` automatically (skip a single push with `git push --no-verify`).
4. Open a PR with a Conventional Commit message

## Cross-repo PRs (core ↔ ha-hemm)

When a change spans both repos, **the core PR lands first** — ha-hemm's CI
installs the core from `hemm-ems/hemm@main`, so ha-hemm's checks (e.g. missing
imports like `PoolPumpManifest`, new `DeviceType` values) stay red until the
core change is on `main`.

**Merge order — follow it exactly to avoid a deadlock:**

1. Merge the **core** PR (`hemm-ems/hemm`) into core `main`.
2. **Re-run** this repo's failed checks (`gh run rerun <id> --failed`). The
   re-run reinstalls core `main` and picks up the new code.
3. Once green, merge the **ha-hemm** PR. Required checks are `Lint`,
   `Unit Tests (Python 3.12)`, and `Unit Tests (Python 3.13)`.
4. Cross-repo traceability reconciles on the nightly run — no manual step.

The canonical version of this playbook, including the required-vs-non-required
check rule on the core side, lives in
[`hemm-ems/hemm` CONTRIBUTING.md](https://github.com/hemm-ems/hemm/blob/main/CONTRIBUTING.md#cross-repo-prs-core--ha-hemm).

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `test:` adding/updating tests
- `chore:` tooling, CI, dependencies
- `refactor:` code change that neither fixes a bug nor adds a feature

## Code Style

- Ruff for linting and formatting (configured in `pyproject.toml`)
- All tests require a marker (`unit`, `container`, `pi`, `slow`, `warp`, `sim`)
