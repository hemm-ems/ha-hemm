# HEMM Onboarding Guide

HEMM (Distributed Energy Optimizer for Home Automation) optimizes when your devices consume or store energy, using dynamic electricity prices, solar forecasts, and your constraints. It outputs standard Home Assistant sensors and relies entirely on HA automations, scripts, and template sensors for actuation and logic. There is no proprietary UI, no cloud dependency, and no black box.

This document explains the principles, walks through two real examples (simple → full house), and shows what HA objects you'll create.

---

## Principles

### 1. HA-Native Glue — No Proprietary Layer

HEMM does not control your devices directly. Instead:

- **Solver outputs** land in standard HA sensors: `sensor.hemm_<device>_plan` (kW), `sensor.hemm_<device>_confidence` (%), `sensor.hemm_<device>_mode` (heat/idle/charge/...).
- **Actuation** happens via HA scripts that *you* write. HEMM calls `script.hp_heat_mode` — what that script does (call a Vaillant API, toggle a Shelly relay, send an IR command) is your business.
- **Constraints** (demands like "EV to 80% by 7 AM") are registered by HA automations calling `hemm.add_constraint_window`. When the condition ends (EV unplugged), the automation calls `hemm.remove_constraint`.
- **Dashboard** uses standard HA entities — any Lovelace card that reads sensors works.

If you can write an HA automation, you can use HEMM. The tools are the ones you already know.

### 2. Adjustability for Everyone — Tiered Configuration

Every device type supports beginner and pro configuration tiers. You can mix tiers per device in the same house.

| Tier | What you enter | HEMM fills in |
|------|---------------|---------------|
| **Beginner** | Floor area, insulation class (good/medium/poor) | Thermal mass, U-value from reference tables |
| **Pro** | Direct U-values, COP curves, thermal mass, efficiency maps | Nothing — you specify everything |

A heat pump on pro tier (with a measured COP curve) can coexist with a room on beginner tier (just "35 m², good insulation"). Start simple, refine later.

### 3. Quirk Management at HA Level — Not in the Energy Manager

HEMM core has **zero vendor-specific code**. No Vaillant defrost workaround, no SMA battery quirk, no Fronius API oddity. All vendor logic lives in HA automations and scripts:

| Quirk | How it's handled |
|-------|-----------------|
| Heat pump enters defrost cycle | Automation watches `binary_sensor.hp_defrosting` → calls `hemm.add_constraint_window` with `forbidden_window` → removes constraint when defrost ends |
| Hot water tank needs legionella prevention | Automation triggers daily → adds `reach_min_temp_once` constraint with deadline |
| Utility forbids battery discharge during peak | Time-based automation adds `forbidden_window` for battery during 17:00–21:00 |
| EV charger needs 30s pause between start/stop | Script includes `delay: 30` — HEMM never sees this detail |

**Why this matters for the community:** Vendor coverage scales with the number of people sharing automations, not with PRs to HEMM core. Someone with a Daikin heat pump shares their defrost automation — any Daikin user copies it. No code review, no release cycle.

### 4. Safe Defaults — Fail to Safe, Not to Off

Every device manifest **must** include a `safe_default` section pointing to an HA script. If HEMM crashes, can't reach the solver, or encounters an error, it calls this script. Examples:

- Battery: hold current SoC (idle)
- Heat pump: hand control back to factory thermostat at 18°C flow temp
- Water heater: normal operation (50°C setpoint)
- EV charger: stop charging

The safe default is the *first* thing you configure for each device. It's the answer to "what happens if HEMM dies at 3 AM?"

### 5. Dry-Run Everything

Every HEMM service accepts `dry_run: true`. The solver runs, produces plans, fires events — but nothing gets actuated. Use this to:

- Verify a new device config produces sensible plans before going live
- Test constraint logic (does the EV actually reach 80%?)
- Monitor optimizer health via the `dry_run_verification` example automation (runs every 4 hours)

### 6. Numeric Conflict Resolution

When constraints compete (EV needs power *and* heat pump needs power, but grid import is limited), HEMM resolves by `priority_penalty` — a number, not a vague "high/medium/low" label.

- Legionella prevention at priority 10.0 beats heat pump minimum runtime at 4.0
- EV departure at priority 7.0 beats battery morning SoC at 3.0
- You set the numbers. You can change them at runtime via `hemm.bump_priority`

This is transparent and debuggable: look at the priorities, understand the outcome.

### 7. Time-Windowed Constraints — Dynamic, Not Permanent

Constraints are not permanent rules ("always charge EV overnight"). They are **dynamic demands with deadlines**:

- EV plugged in at 18:00 → automation adds "80% by 07:00" constraint
- EV unplugged at 07:15 → automation removes the constraint
- Next day, EV plugged in at 22:00 → new constraint, different deadline

This matches reality: your needs change daily. The solver adapts every 15 minutes.

---

## How It Works (Architecture in 30 Seconds)

```
┌─────────────────────────────────────────────────────┐
│                  Home Assistant                       │
│                                                       │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────┐ │
│  │ Automations   │   │ Scripts      │   │ Sensors  │ │
│  │               │   │              │   │          │ │
│  │ EV plugged →  │   │ hp_heat_mode │   │ hemm_*   │ │
│  │ add_constraint│   │ ev_start     │   │ _plan    │ │
│  │               │   │ bat_charge   │   │ _mode    │ │
│  └──────┬───────┘   └──────▲───────┘   └────▲─────┘ │
│         │                   │                │       │
│         ▼                   │                │       │
│  ┌──────────────────────────┴────────────────┴─────┐ │
│  │              HEMM Integration                    │ │
│  │  Coordinator → Solver → Plans → Sensors          │ │
│  │  Services ← Automations                          │ │
│  └──────────────────────┬──────────────────────────┘ │
│                         │                             │
│                         ▼                             │
│  ┌──────────────────────────────────────────────────┐ │
│  │              HEMM Core Library                    │ │
│  │  Manifests · Constraints · MILP Solver            │ │
│  │  Forecast Adapters · Verification Contracts       │ │
│  └──────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

1. **Manifests** describe each device: what it can do, its physical parameters, its safe default
2. **Solver** reads manifests + active constraints + price forecast → produces 24-hour plans (15-min slots)
3. **HA sensors** expose the current plan; HA automations/scripts act on it

---

## Example 1: Simple Setup (PV + Battery + EV + Thermostat)

This is the canonical "first setup" — the `onboarding` scenario. It matches `hemm/testdata/scenarios/onboarding.yaml` exactly and is continuously tested (see `hemm/tests/test_onboarding_examples.py`).

### Devices

| Device | Manifest | What it does |
|--------|----------|-------------|
| PV system | `pv_forecast.json` | 9.8 kWp rooftop, Solcast forecast. No actions — just a producer. |
| House battery | `battery.json` | 10 kWh, 5 kW charge/discharge, 95% efficiency. Actions: charge, discharge, idle. |
| EV charger | `ev_charger.json` | 11 kW (3-phase), 77 kWh vehicle battery. Actions: charge, stop. |
| Thermostat | `thermostat_load.json` | 1.2 kW bathroom towel radiator. Actions: allow (open relay), block (close relay). |

### Constraints

| Constraint | Device | Deadline | Requirement | Priority |
|-----------|--------|----------|-------------|----------|
| `ev_morning_charge` | ev_charger_garage | 07:00 | SoC ≥ 80% | 5.0 |
| `thermostat_comfort` | bathroom_heater | 23:59 | Temperature 20–23°C | 3.0 |

### What the Solver Does

Given a dynamic tariff (base €0.30, peak €0.42, off-peak €0.18):

1. **Battery arbitrage:** Charge from PV during midday (free solar), discharge during evening peak (avoid €0.42/kWh grid import)
2. **EV scheduling:** Charge overnight during off-peak hours (€0.18/kWh) to reach 80% by 07:00 — not immediately when plugged in at full price
3. **Thermostat shifting:** Allow heating during cheap hours, block during peak — within the 20–23°C comfort band

Expected solve time: < 1 second.

### What You Build in HA

**4 Safe-Default Scripts** (one per controllable device):

```yaml
# scripts.yaml
script:
  battery_safe_default:
    alias: "Battery Safe Default"
    sequence:
      - service: switch.turn_off
        target:
          entity_id: switch.battery_inverter

  ev_safe_default:
    alias: "EV Safe Default"
    sequence:
      - service: switch.turn_off
        target:
          entity_id: switch.ev_charger_relay

  thermostat_safe_default:
    alias: "Thermostat Safe Default"
    sequence:
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.bathroom_towel_heater
        data:
          hvac_mode: "off"
```

**1 Automation — EV Plug-In Detection** (registers the constraint):

```yaml
# automations.yaml
automation:
  - alias: "HEMM: EV Plugged In → Schedule Charge"
    trigger:
      - platform: state
        entity_id: binary_sensor.ev_plugged_in
        to: "on"
    action:
      - service: hemm.add_constraint_window
        data:
          window_id: "ev_morning_{{ now().strftime('%Y%m%d') }}"
          device_id: ev_charger_garage
          deadline: >
            {{ today_at('07:00') if now().hour < 7
               else (now() + timedelta(days=1)).strftime('%Y-%m-%dT07:00:00') }}
          requirement_type: min_soc_until
          requirement_params:
            min_soc_pct: 80
          priority_penalty: 5.0
      - service: hemm.replan

  - alias: "HEMM: EV Unplugged → Remove Constraint"
    trigger:
      - platform: state
        entity_id: binary_sensor.ev_plugged_in
        to: "off"
    action:
      - service: hemm.remove_constraint
        data:
          window_id: "ev_morning_{{ now().strftime('%Y%m%d') }}"
```

**Template Sensors for Dashboard** (optional, for Lovelace cards):

```yaml
# configuration.yaml
template:
  - sensor:
      - name: "EV Charge Power"
        unit_of_measurement: "kW"
        state: "{{ states('sensor.hemm_ev_charger_plan') | float(0) }}"
      - name: "Battery Next Action"
        state: >
          {% set power = states('sensor.hemm_house_battery_plan') | float(0) %}
          {% if power > 0 %}Charging{% elif power < 0 %}Discharging{% else %}Idle{% endif %}
      - name: "Optimizer Confidence"
        unit_of_measurement: "%"
        state: "{{ states('sensor.hemm_house_battery_confidence') | float(0) }}"
```

### Minimal Dashboard Card

```yaml
# Lovelace card
type: entities
title: HEMM Energy Plan
entities:
  - entity: sensor.hemm_house_battery_plan
    name: Battery
  - entity: sensor.hemm_ev_charger_plan
    name: EV Charger
  - entity: sensor.hemm_thermostat_living_plan
    name: Thermostat
  - entity: sensor.hemm_house_battery_confidence
    name: Confidence
  - entity: sensor.hemm_house_battery_mode
    name: Battery Mode
```

---

## Example 2: Full House (All 7 Device Types)

This is the `full_house` scenario — all 7 manifest types active simultaneously. It matches `hemm/testdata/scenarios/full_house.yaml` and is the mandatory complexity test (see `hemm/tests/test_onboarding_examples.py`).

### Devices

| Device | Type | Key Parameters |
|--------|------|---------------|
| Living room | Room | 35 m², 2.5 kWh/K thermal mass, south-facing windows |
| Bathroom heater | ThermostatLoad | 1.2 kW, hysteresis-based on/off |
| Main heat pump | HeatPump | 5 kW, COP 2.0–5.0, defrost lockout 10 min |
| Hot water tank | WaterHeater | 200L, 3 kW, 45W standby loss |
| House battery | Battery | 10 kWh, 5 kW, 95% round-trip |
| Rooftop PV | PVForecast | 9.8 kWp, Solcast adapter |
| Garage EV charger | EVCharger | 11 kW, 77 kWh vehicle |

### Constraints (4 Competing Demands)

| Constraint | Device | Requirement | Priority | Why this priority |
|-----------|--------|-------------|----------|-------------------|
| `battery_morning_soc` | house_battery | SoC ≥ 60% by 07:00 | 3.0 | Nice to have — backup power for morning |
| `ev_departure` | ev_charger_garage | ≥ 25 kWh delivered by 07:30 | 7.0 | Must have — commute depends on it |
| `hp_runtime` | heat_pump_main | ≥ 4 hours runtime/day | 4.0 | Comfort — but heat pump can catch up later |
| `wh_legionella` | dhw | Reach 60°C once today | **10.0** | Safety — non-negotiable |

### How Priority Resolves Conflicts

At 03:00 on a cold night (0°C outside), multiple devices want power:

1. **Legionella (p=10.0)** wins — water heater gets 3 kW to reach 60°C. Non-negotiable safety requirement.
2. **EV departure (p=7.0)** gets remaining import capacity — the commute can't wait.
3. **Heat pump runtime (p=4.0)** defers to cheaper morning hours when solar starts — 4 hours is achievable across the full day.
4. **Battery morning SoC (p=3.0)** charges from early-morning PV if possible, accepts partial charge if constrained.

The solver makes this trade-off every 15 minutes. You see it in the sensor values; you debug it by looking at priorities.

### Additional HA Objects for Full House

Beyond the simple setup, the full house adds:

**Quirk Automations:**

```yaml
# Heat pump defrost lockout — vendor-specific quirk handled in HA
automation:
  - alias: "HEMM: HP Defrost → Forbidden Window"
    trigger:
      - platform: state
        entity_id: binary_sensor.hp_defrosting
        to: "on"
    action:
      - service: hemm.add_constraint_window
        data:
          window_id: "hp_defrost_{{ now().timestamp() | int }}"
          device_id: heat_pump_main
          deadline: "{{ (now() + timedelta(minutes=15)).isoformat() }}"
          requirement_type: forbidden_window
          priority_penalty: 20.0
      - service: hemm.replan

# Legionella prevention — daily check
  - alias: "HEMM: Daily Legionella Check"
    trigger:
      - platform: time
        at: "03:00:00"
    action:
      - service: hemm.add_constraint_window
        data:
          window_id: "legionella_{{ now().strftime('%Y%m%d') }}"
          device_id: dhw
          deadline: "{{ (now() + timedelta(hours=6)).isoformat() }}"
          requirement_type: reach_min_temp_once
          requirement_params:
            target_temp_c: 60
          priority_penalty: 10.0
      - service: hemm.replan
```

**Room Comfort — Adaptive Based on Presence:**

```yaml
automation:
  - alias: "HEMM: Away → Relax Comfort Band"
    trigger:
      - platform: state
        entity_id: group.household
        to: "not_home"
    action:
      - service: hemm.remove_constraint
        data:
          window_id: "room_comfort_today"
      - service: hemm.add_constraint_window
        data:
          window_id: "room_comfort_away"
          device_id: room_living
          deadline: "{{ today_at('23:59').isoformat() }}"
          requirement_type: hold_temp_band
          requirement_params:
            min_temp_c: 16.0
            max_temp_c: 25.0
          priority_penalty: 1.0
      - service: hemm.replan
```

---

## First 15 Minutes — Quick Start

### 1. Install (2 min)

HACS support is coming soon. For now, install manually:

1. Download the [latest release](https://github.com/swifty99/ha-hemm/releases/latest) and unzip it.
2. Copy the `custom_components/hemm/` directory into your HA configuration directory at `config/custom_components/hemm/`. You can do this via the [File Editor add-on](https://www.home-assistant.io/integrations/file_editor/), a Samba share, or SFTP — no SSH required.
3. Restart Home Assistant. HA automatically installs the `hemm` Python library on first load.

### 2. Add the Hub (1 min)

Settings → Integrations → Add → HEMM Energy Optimizer.

Configure:
- **Name:** "My Home" (or whatever)
- **Optimization horizon:** 24 hours (default)
- **Price adapter:** Solcast / Forecast.Solar / Template
- **Solver backend:** MILP Central (default, recommended)

### 3. Add Your First Device — Battery (3 min)

Settings → Integrations → HEMM → Configure → Add Device → Battery → Beginner.

Enter:
- **Name:** "House Battery"
- **Capacity:** 10 kWh
- **Max charge/discharge:** 5 kW
- **Safe default script:** `script.battery_safe_default` (create this script first — it should set the battery to idle)

### 4. Dry-Run (2 min)

Developer Tools → Services → `hemm.simulate`:

```yaml
service: hemm.simulate
data:
  dry_run: true
```

Check: does `sensor.hemm_house_battery_plan` show a value? Does `sensor.hemm_house_battery_confidence` show > 0%?

### 5. Add More Devices (5 min)

Repeat step 3 for each device. Start with what you have — you don't need all 7 types.

### 6. Go Live

Once dry-runs look right, the coordinator runs automatically every 15 minutes. Watch the sensors, check the logs, adjust priorities.

---

## Comparison with Alternatives

| | HEMM | EMHASS | Manual (Node-RED / AppDaemon) |
|---|------|--------|-------------------------------|
| **Device config** | Declarative manifest (JSON) | YAML config per device | Custom code per device |
| **Solver** | MILP (optimal) + Distributed (experimental) | Linear programming | Rule-based (heuristic) |
| **Actuation** | HA scripts (user-written) | Direct entity calls | Direct entity calls |
| **Vendor knowledge** | Zero in core — lives in HA automations | Some built-in device models | Whatever you code |
| **Constraint model** | Time-windowed, priority-weighted, dynamic | Static schedules | Whatever you code |
| **New device type** | Add a manifest JSON | Modify config + code | Write new rules |
| **Conflict resolution** | Numeric priority (transparent) | Not applicable (single-device focus) | Whatever you code |
| **Verification** | Built-in contracts (expected outcome checks) | None | Whatever you code |
| **Safe defaults** | Mandatory per device | Not enforced | Whatever you code |
| **Dry-run** | Every service, always available | Partial | Whatever you code |

HEMM's architectural bet: **the energy manager should not know about vendors**. Vendor-specific logic changes faster than any integration can keep up. By pushing quirks to HA automations (which users already write and share), HEMM avoids the maintenance trap where every heat pump firmware update requires a core patch.

---

## Troubleshooting First Setup

### "safe_default_required" error when adding a device

Every device needs a safe default script. Create the script *first* in HA (Settings → Automations & Scenes → Scripts → Create), then reference it when adding the device. The script should put the device in a known-safe state (idle, off, factory thermostat mode).

### Solver returns INFEASIBLE

The constraints can't all be met simultaneously. Common causes:
- EV needs 60 kWh but only 8 hours remain until deadline at 11 kW max → physically impossible
- Comfort band too tight with insufficient heating capacity
- Multiple high-priority constraints competing for the same power budget

Fix: relax the hardest constraint (lower the SoC target, widen the comfort band, extend the deadline) or lower its `priority_penalty` so other constraints can win.

### Forecast adapter not responding (sensor stays "unknown")

- **Solcast:** Check your API key in the Solcast integration. Free tier allows 10 requests/day.
- **Forecast.Solar:** Verify latitude/longitude/azimuth/tilt. The API is rate-limited.
- **Template fallback:** Use a Jinja2 template expression as a forecast source while debugging other adapters.

### Plans look wrong (battery charges during peak)

1. Check the price forecast: Developer Tools → States → search for your price entity. Is it populated?
2. Run `hemm.simulate` with `dry_run: true` and check the logs for constraint/price data.
3. Verify the battery manifest has correct `charge_efficiency` and `discharge_efficiency` — values of 1.0 (impossible) will produce strange plans.

### Solver timeout (> 60 seconds)

Usually happens with 7+ devices on a Raspberry Pi. Options:
- Reduce `horizon_hours` from 24 to 12
- Increase `resolution_minutes` from 15 to 30 (halves the problem size)
- The `solver_degraded` repair issue will appear automatically — follow its guidance

---

## What's Tested

Both examples in this guide are mandatory tests that run on every commit:

- `hemm/tests/test_onboarding_examples.py::test_onboarding_scenario_solves` — loads `onboarding.yaml`, asserts solver finds a valid plan in < 1 second
- `hemm/tests/test_onboarding_examples.py::test_full_house_scenario_solves` — loads `full_house.yaml`, asserts all 7 devices get plans
- `hemm/tests/test_onboarding_examples.py::test_onboarding_constraints_met` — verifies EV reaches target SoC by deadline
- `hemm/tests/test_onboarding_examples.py::test_full_house_priority_ordering` — verifies highest-priority constraint (legionella) is satisfied

If these tests break, the examples in this guide are wrong. If the examples work, these tests pass. Living documentation.
