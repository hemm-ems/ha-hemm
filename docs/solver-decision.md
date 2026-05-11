# HEMM Solver A/B Decision — Phase 6 Gate

**Date:** 2026-05-11  
**Decision:** Ship Backend A (Central MILP) as default; keep Backend B (Distributed) as experimental option.

## Test Protocol

Ran identical scenarios through both backends using `hemm sim compare` across all 6 standard scenarios:
- onboarding (PV + Battery + EV + Thermostat)
- battery_arbitrage (single battery)
- heat_pump_shift (heat pump + room)
- ev_departure (EV with departure deadline)
- water_heater_legionella (legionella cycle)
- full_house (all 7 device types)

## Results Summary

| Metric | Threshold | Result | Pass |
|--------|-----------|--------|------|
| Cost gap (avg B vs A) | < 3% | 96.20% | **FAIL** |
| Comfort violations | B ≤ A | 0 scenarios worse | PASS |
| Plan stability | ≤ 1.5× A | 1.00× | PASS |

### Per-Scenario Breakdown

| Scenario | Cost A (€) | Cost B (€) | Gap % | Time A | Time B | B Converged |
|----------|-----------|-----------|-------|--------|--------|-------------|
| onboarding | −3.45 | −4.65 | −34.8% | 3.578s | 0.078s | No |
| battery_arbitrage | −4.56 | −1.77 | +61.1% | 0.282s | 0.047s | Yes |
| heat_pump_shift | 0.00 | 13.38 | +100.0% | 0.906s | 0.062s | No |
| ev_departure | 0.00 | 3.42 | +100.0% | 0.250s | 0.016s | No |
| water_heater_legionella | 0.00 | 0.43 | +100.0% | 0.125s | 0.016s | No |
| full_house | −4.13 | 6.23 | +250.9% | 0.750s | 0.093s | No |

## Analysis

### Backend A (Central MILP — Pyomo + HiGHS)
- Produces provably optimal solutions across all scenarios.
- Battery arbitrage achieves €4.56 savings, full_house achieves €4.13.
- Heat pump, EV, and water heater scenarios correctly converge to cost-neutral (no unnecessary operation).
- Solve times range 0.1–3.6 s (well within 60 s time limit).

### Backend B (Distributed — Price Iteration / ADMM)
- 10× faster than A (avg speed ratio 0.10×), but heuristic quality gap is large.
- Converged on only 1/6 scenarios (battery_arbitrage — the simplest).
- Overproduces energy: heat_pump_shift = €13.38, full_house = €6.23 (vs €0 / −€4.13).
- Root cause: consumer models are greedy heuristics that don't jointly optimize.
  Each consumer independently responds to price signals without a global optimality guarantee.
- No comfort violations (constraint safety layer works), but cost optimality is lost.

### Why the gap is expected at this stage
The distributed solver's consumer models use simple price-response heuristics (charge below median price, etc.). These are correct for single-device optimization but produce suboptimal coordination in multi-device scenarios. This is a known trade-off: the architecture is designed for future ML-enhanced consumer models (Phase 9+) that learn device interactions.

## Decision

1. **Default backend:** `milp_central` (Backend A). All user-facing optimization uses this.
2. **Backend B remains available** via `hemm.set_solver` service and config flow for:
   - Speed-sensitive use cases (Pi hardware with >10 devices)
   - Research/experimentation
   - Future ML consumer model development
3. **No solver auto-switch.** Users must explicitly choose distributed mode.
4. **Config flow default:** `milp_central` (already set in `const.py`).
5. **Re-evaluate** Backend B after Pi validation (Phase 8) and ML consumer models.

## Gate Criteria for Future B Promotion

- Cost gap < 3% across standard scenarios
- Convergence ≥ 5/6 scenarios
- Speed advantage maintained (≥ 5×)
