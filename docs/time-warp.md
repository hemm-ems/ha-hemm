# Time-warp mode (Docker)

Run the whole HA + HEMM stack at an arbitrary speed multiplier — from a
fixed 100× to a PI-controlled "as fast as the host allows" auto mode.
Used for regression testing the integration against a synthetic home
and for future end-user "what-if" simulations.

## How it works

A small purpose-built LD_PRELOAD shim (`warp-lib/warp.c`) is compiled into
the HA container at build time. It does two things — both necessary for the
scheduler to actually run faster:

1. **Virtualizes clock reads**: `clock_gettime(CLOCK_REALTIME)` and
   `clock_gettime(CLOCK_MONOTONIC)` (plus `gettimeofday`, `time`, and the
   coarse variants) all return `anchor + (real_elapsed × speed)`. So HA's
   `dt_util.utcnow()`, Python's `time.monotonic()`, and asyncio's
   `loop.time()` all observe accelerated time.
2. **Scales sleep/wait timeouts**: `epoll_wait`, `poll`, `ppoll`, `select`,
   `pselect`, `nanosleep`, `clock_nanosleep` all have their timeout argument
   divided by speed before the kernel sees it. So when asyncio asks the
   kernel "wait up to 900 seconds for the next event", the kernel actually
   waits 9 wall-seconds at 100×.

Why not `libfaketime`? It only does (1), not (2). HA's asyncio loop blocks
in `epoll_wait` between events, and the kernel measures that wait in real
time regardless of what userspace believes about clocks. With libfaketime
alone, `dt_util.utcnow()` would read fast but the coordinator would still
refresh every 15 real-minutes. The (2) piece is the difference between "the
clock displays fast" and "the application actually runs fast."

## Modes

### Fixed mode

Set `WARP_SPEED=100` (or any positive number) for a constant speed
multiplier. The speed is read once at process start and never changes.
Good for deterministic, reproducible tests.

### Auto mode (default)

Leave `WARP_SPEED` empty or unset. A background PI-controller thread inside
the LD_PRELOAD shim reads cgroup v2 CPU stats
(`/sys/fs/cgroup/cpu.stat → usage_usec`) and continuously adjusts the speed
multiplier to keep the container's single-CPU utilization near a target
(default 50%).

Speed starts at 1.0× and ramps up as fast as the slew rate limiter allows.
On a modern laptop HA typically settles at the configured maximum (default
1000×) within ~10 seconds because HA's idle CPU usage is far below 50%.

The PI controller parameters are tuned for stability:

- **Kp = 10**, **Ki = 1** — gentle proportional + integral action
- **Slew rate = 200×/s** — maximum speed change per second, prevents jumps
- **Anti-windup** — integral accumulation is undone when speed is at bounds
- **3-second boot delay** — the PI thread sleeps 3s after init to let HA
  boot before measuring CPU

**Speed file sharing**: the PI controller writes the current speed to
`/tmp/.warp_speed` every tick. Short-lived processes (e.g. `docker exec`)
read this file at init to inherit the main process's speed instead of
starting at 1.0. Only the main HA process spawns the PI thread; child
processes run at the inherited fixed speed.

## Quick start

```sh
cd ha-hemm

make warp-up              # build image, start stack (auto mode by default)

make warp-clock           # wall vs container clock comparison

make warp-logs            # tail HA logs (timestamps progress at warp rate)

make warp-shell           # exec into the container

make warp-down            # tear down
```

Fixed speed:

```sh
WARP_SPEED=500 make warp-up               # fixed 500× speed
WARP_START_REAL=1838246400 make warp-up    # start at 2028-04-12 UTC
```

Auto mode with custom target:

```sh
WARP_CPU_TARGET=0.7 make warp-up          # target 70% CPU utilization
WARP_SPEED_MAX=500 make warp-up           # cap auto speed at 500×
```

## Verification

```sh
make test-warp
```

brings up the stack, runs the warp smoke tests (`tests/warp/`), and tears
down on exit. The suite covers:

- `test_container_running` — container healthy
- `test_warp_lib_loaded` — libwarp.so is the HA process's LD_PRELOAD
- `test_virtual_clock_advances_at_warp_speed` — clock reads accelerate
- `test_asyncio_sleep_scales_with_speed` — `asyncio.sleep(1)` returns in
  ~1/speed wall-seconds (this is the scheduler-acceleration check)
- `test_ha_scheduler_fires_automation_at_warp_rate` — the `warp-heartbeat`
  time-pattern automation fires `speed/60` times per wall-second
- `test_virtual_clock_in_2026_or_later` — `WARP_START_REAL` anchor honored
- `test_no_unrecoverable_errors_in_ha_log` — HA boots clean under libwarp

Measured on a dev laptop:
- 5 × `asyncio.sleep(1)` baseline (no LD_PRELOAD): ~5.4s wall
- 5 × `asyncio.sleep(1)` at WARP_SPEED=100: ~0.36s wall (mostly container start)
- 5 × `asyncio.sleep(1)` at WARP_SPEED=1000: ~0.27s wall
- 1 virtual hour of `await asyncio.sleep(1)` polling at WARP_SPEED=100: 36s wall

## Synthetic home

`tests/warp/config/configuration.yaml` defines a minimal simulated home:

| Entity | Behavior |
|--------|----------|
| `sensor.warp_outdoor_temp` | cosine day/night curve in `now().hour` |
| `sensor.warp_pv_power` | gaussian peaking at noon |
| `sensor.warp_price` | dynamic tariff with morning/evening peaks |
| `sensor.warp_battery_soc` | reads `input_number.warp_battery_soc_pct` |
| `sensor.warp_indoor_temp` | reads `input_number.warp_indoor_temp_c` |
| `input_number.warp_battery_soc_pct` | mutable; closed-loop sim service can update it |
| `input_number.warp_indoor_temp_c` | same |
| `automation.warp_heartbeat` | fires every virtual minute, logs a warning |

Template sensors that reference `now()` re-evaluate on a HA-managed schedule
that fires on virtual minute boundaries — under warp those minute boundaries
arrive at wall-cadence `speed/60` per second.

## Knobs

### General

| Env var | Default | Meaning |
|---------|---------|---------|
| `WARP_SPEED` | _(empty = auto)_ | Speed multiplier. Set a number for fixed mode; leave empty for auto mode. |
| `WARP_START_REAL` | `1778544000` (2026-05-12 00:00 UTC) | Virtual `CLOCK_REALTIME` anchor as Unix-epoch float. |
| `DISABLE_JEMALLOC` | `1` (set in Dockerfile.warp) | HA's entrypoint skips its jemalloc LD_PRELOAD, leaving `libwarp.so` as the active preload. |

### Auto mode (PI controller)

| Env var | Default | Meaning |
|---------|---------|---------|
| `WARP_CPU_TARGET` | `0.5` | Target single-CPU utilization (0.0–1.0). |
| `WARP_SPEED_MIN` | `1.0` | Minimum speed the PI controller will set. |
| `WARP_SPEED_MAX` | `1000.0` | Maximum speed the PI controller will set. |
| `WARP_KP` | `10.0` | Proportional gain. |
| `WARP_KI` | `1.0` | Integral gain. |
| `WARP_PI_INTERVAL` | `50` | PI loop interval in milliseconds. |
| `WARP_SLEW` | `200.0` | Maximum speed change per second (slew rate limiter). |

The PI controller reads cgroup v2 `cpu.stat` (`usage_usec`), computes
single-CPU utilization as `Δcpu_usec / Δwall_usec`, and adjusts speed with:

```
error = target - cpu_util
integral += error × dt
output = Kp × error + Ki × integral
new_speed = clamp(current + slew_limit(output), min, max)
```

In fixed mode, all auto-mode knobs are ignored.

## Known limitations

1. **`busybox date` shows wall-clock time, not virtual.** busybox's `date`
   uses code paths that don't go through libc's `clock_gettime` symbol, so
   our LD_PRELOAD doesn't intercept. Python/HA do, so this is cosmetic.
   `docker exec hemm-ha-warp python3 -c 'import datetime; print(datetime.datetime.now())'`
   shows the warped time correctly.

2. **No jemalloc.** HA's image normally LD_PRELOADs `libjemalloc.so.2`;
   chaining it with our shim caused boot to hang. We set `DISABLE_JEMALLOC=1`
   and accept slightly slower allocation. For a sim-only container that's a
   fair trade.

3. **Speed changes in fixed mode require restart.** `WARP_SPEED` is read
   once on first clock call. Use `make warp-set-speed SPEED=500` to restart
   with a new value. In auto mode, speed is continuously adjusted.

4. **TLS certificate validity.** HA's self-signed cert is valid for years;
   the default anchor (`2026-05-12`) keeps simulations inside that window.
   Starting too far in the future may trip cert checks.

5. **Cgroup v2 required for auto mode.** The PI controller reads
   `/sys/fs/cgroup/cpu.stat` (cgroup v2). On hosts with cgroup v1 only,
   auto mode will start at speed 1.0 and stay there. Use fixed mode on
   such hosts.

## Architecture notes

The companion in-process `Clock` abstraction in `hemm/src/hemm/time/` and
`ha-hemm/custom_components/hemm/time.py` is complementary, not redundant:

- **In-process `Clock`** — used by unit tests (no Docker) and by future
  end-user "what-if" runs that need a private virtual clock without
  affecting real HA state.
- **libwarp / Docker time-warp** (this doc) — runs the whole real HA
  binary at warp speed for integration / smoke / scenario regression.

The `check_clock` AST audit (wired into both repos' `make ci`) ensures every
time read in domain code routes through the `Clock` abstraction — so when
the in-process path is needed, it's always available.
