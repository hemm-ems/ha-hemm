/*
 * warp.so — minimal LD_PRELOAD that runs the host application at WARP_SPEED× wall-clock.
 *
 * What libfaketime does (and where it falls short):
 *   libfaketime virtualizes the *clock-read* syscalls (clock_gettime, gettimeofday,
 *   time, etc) so a process sees an accelerated wall clock. That's only half the
 *   story. asyncio (and any code using select/poll/epoll/nanosleep) tells the
 *   kernel "wait up to N seconds for an event". The kernel measures that N in
 *   real time. So even with virtualized clocks, the event loop blocks for real
 *   seconds between events — the scheduler does NOT accelerate.
 *
 * What this library does:
 *   1. Virtualizes CLOCK_REALTIME, CLOCK_MONOTONIC, CLOCK_BOOTTIME (and their
 *      coarse variants) and the gettimeofday/time legacy APIs:
 *          virt_real = real_anchor + (real_now - process_start_real) * speed
 *          virt_mono = (real_now - process_start_real) * speed + process_start_real
 *      (Monotonic starts at the same value clock_gettime would have returned at
 *      process start, so existing code that captured an "epoch" stays consistent.)
 *   2. Scales the timeout argument of every wait/sleep syscall by 1/speed before
 *      handing it to the kernel:
 *          epoll_wait(.., timeout_ms)  → epoll_wait(.., timeout_ms / speed)
 *          poll, ppoll, select, pselect, nanosleep, clock_nanosleep — same.
 *      So an asyncio loop that wants "wait 900 virtual seconds for next handle"
 *      ends up calling epoll_wait(timeout = 9 real seconds) at speed=100×.
 *
 * Modes:
 *   Fixed:  WARP_SPEED env var set → constant speed (original behavior).
 *   Auto:   WARP_SPEED unset or empty → a background PI-controller thread
 *           reads cgroup CPU stats and adjusts speed to hit WARP_CPU_TARGET.
 *           Speed starts at 1.0 and ramps up as the host allows.
 *
 * Knobs (env vars, read once on first clock call):
 *   WARP_SPEED       — speed multiplier; >1 makes time pass faster (default: auto)
 *   WARP_START_REAL  — virtual CLOCK_REALTIME anchor as a Unix-epoch seconds float
 *                      (default: real time at process start, no offset)
 *
 * Auto-mode knobs:
 *   WARP_CPU_TARGET  — target CPU utilization 0.0-1.0 (default 0.5)
 *   WARP_SPEED_MIN   — minimum speed (default 1.0)
 *   WARP_SPEED_MAX   — maximum speed (default 1000.0)
 *   WARP_KP          — proportional gain (default 10.0)
 *   WARP_KI          — integral gain (default 1.0)
 *   WARP_PI_INTERVAL — PI loop interval in ms (default 50)
 *   WARP_SLEW        — max speed change per second (default 200.0)
 *
 * Compile:
 *   apk add --no-cache gcc musl-dev linux-headers
 *   gcc -O2 -Wall -fPIC -shared -o /usr/lib/libwarp.so warp.c -ldl -lpthread
 *
 * Use:
 *   LD_PRELOAD=/usr/lib/libwarp.so WARP_SPEED=100 WARP_START_REAL=1778544000 <cmd>
 *   LD_PRELOAD=/usr/lib/libwarp.so WARP_START_REAL=1778544000 <cmd>   # auto mode
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/select.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

/* ---------- internal state, initialized lazily ----------
 * `g_mono_anchor` is the real (kernel) CLOCK_MONOTONIC reading at init —
 * it's our source of truth for "how much wall time has elapsed since init".
 * `g_realtime_anchor` is the virtual CLOCK_REALTIME we want time to start
 * from (either WARP_START_REAL env var, or the real CLOCK_REALTIME at init).
 * Both monotonic and realtime virtual readings derive from one wall delta:
 *     wall_elapsed = real_mono_now - g_mono_anchor
 *     virt_real    = g_realtime_anchor + wall_elapsed * speed
 *     virt_mono    = g_mono_anchor     + wall_elapsed * speed
 * Using mono as the wall reference avoids surprises if the host's CLOCK_REALTIME
 * gets adjusted by NTP mid-run.
 */
static _Atomic double g_speed = 1.0;
static double g_mono_anchor = 0.0;
static double g_realtime_anchor = 0.0;
static atomic_int g_initialized = 0;
static int g_auto_mode = 0;

/* ---------- real syscall pointers ---------- */
static int (*real_clock_gettime)(clockid_t, struct timespec *) = NULL;
static int (*real_gettimeofday)(struct timeval *, void *) = NULL;
static time_t (*real_time)(time_t *) = NULL;
static int (*real_nanosleep)(const struct timespec *, struct timespec *) = NULL;
static int (*real_clock_nanosleep)(clockid_t, int, const struct timespec *, struct timespec *) = NULL;
static int (*real_epoll_wait)(int, struct epoll_event *, int, int) = NULL;
static int (*real_epoll_pwait)(int, struct epoll_event *, int, int, const sigset_t *) = NULL;
static int (*real_poll)(struct pollfd *, nfds_t, int) = NULL;
static int (*real_ppoll)(struct pollfd *, nfds_t, const struct timespec *, const sigset_t *) = NULL;
static int (*real_select)(int, fd_set *, fd_set *, fd_set *, struct timeval *) = NULL;
static int (*real_pselect6)(int, fd_set *, fd_set *, fd_set *, const struct timespec *, const sigset_t *) = NULL;

#define RESOLVE(sym)                                                                                                   \
    do {                                                                                                               \
        if (!real_##sym) real_##sym = dlsym(RTLD_NEXT, #sym);                                                          \
    } while (0)

/* ---------- helpers ---------- */
static inline double ts_to_d(const struct timespec *ts) { return (double)ts->tv_sec + (double)ts->tv_nsec / 1e9; }

static inline void d_to_ts(double d, struct timespec *ts) {
    if (d < 0) d = 0;
    ts->tv_sec = (time_t)d;
    ts->tv_nsec = (long)((d - (double)ts->tv_sec) * 1e9);
    if (ts->tv_nsec >= 1000000000L) {
        ts->tv_sec++;
        ts->tv_nsec -= 1000000000L;
    }
}

static double real_realtime(void) {
    struct timespec ts;
    RESOLVE(clock_gettime);
    real_clock_gettime(CLOCK_REALTIME, &ts);
    return ts_to_d(&ts);
}

static double real_monotonic(void) {
    struct timespec ts;
    RESOLVE(clock_gettime);
    real_clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts_to_d(&ts);
}

/* ---------- inline speed accessor (atomic, lock-free) ---------- */
static inline double get_speed(void) {
    return atomic_load_explicit(&g_speed, memory_order_relaxed);
}

/* ---------- shared speed file (auto-mode) ---------- */
/* The PI controller writes the current speed to this file so that
 * short-lived processes (e.g. `docker exec ... date`) can inherit
 * the main process's speed instead of starting at 1.0. */
#define WARP_SPEED_FILE "/tmp/.warp_speed"

static void write_speed_file(double speed) {
    FILE *f = fopen(WARP_SPEED_FILE, "w");
    if (f) {
        fprintf(f, "%.6f\n", speed);
        fclose(f);
    }
}

static double read_speed_file(void) {
    FILE *f = fopen(WARP_SPEED_FILE, "r");
    if (!f) return -1.0;
    double speed = -1.0;
    if (fscanf(f, "%lf", &speed) != 1) speed = -1.0;
    fclose(f);
    return speed;
}

/* ---------- PI controller (auto-mode) ---------- */

/* Read cgroup v2 cpu.stat usage_usec. Returns -1 on failure. */
static long long read_cgroup_cpu_usec(void) {
    FILE *f = fopen("/sys/fs/cgroup/cpu.stat", "r");
    if (!f) return -1;
    char line[128];
    long long usec = -1;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, "usage_usec ", 11) == 0) {
            usec = atoll(line + 11);
            break;
        }
    }
    fclose(f);
    return usec;
}

static void *pi_controller_thread(void *arg) {
    (void)arg;

    /* Parse PI config from env */
    const char *e;
    double cpu_target = 0.5;
    double speed_min = 1.0;
    double speed_max = 1000.0;
    double Kp = 10.0;
    double Ki = 1.0;
    int interval_ms = 50;
    double slew_per_sec = 200.0;  /* max speed change per wall-second */

    e = getenv("WARP_CPU_TARGET");  if (e && atof(e) > 0) cpu_target = atof(e);
    e = getenv("WARP_SPEED_MIN");   if (e && atof(e) > 0) speed_min  = atof(e);
    e = getenv("WARP_SPEED_MAX");   if (e && atof(e) > 0) speed_max  = atof(e);
    e = getenv("WARP_KP");          if (e) Kp = atof(e);
    e = getenv("WARP_KI");          if (e) Ki = atof(e);
    e = getenv("WARP_PI_INTERVAL"); if (e && atoi(e) > 0) interval_ms = atoi(e);
    e = getenv("WARP_SLEW");        if (e && atof(e) > 0) slew_per_sec = atof(e);

    if (cpu_target <= 0 || cpu_target >= 1.0) cpu_target = 0.5;
    if (speed_min < 1.0) speed_min = 1.0;
    if (speed_max < speed_min) speed_max = speed_min;

    double max_delta = slew_per_sec * ((double)interval_ms / 1000.0);

    RESOLVE(nanosleep);
    RESOLVE(clock_gettime);

    /* Wait a few seconds for the process to stabilize during boot.
     * This also prevents short-lived `docker exec` processes from
     * printing PI log messages (they exit before the delay ends). */
    struct timespec boot_delay = {3, 0};
    real_nanosleep(&boot_delay, NULL);

    fprintf(stderr, "[warp-pi] auto mode: target=%.0f%% bounds=[%.0f, %.0f] "
            "Kp=%.1f Ki=%.1f interval=%dms slew=%.0f/s\n",
            cpu_target * 100, speed_min, speed_max, Kp, Ki, interval_ms,
            slew_per_sec);

    long long prev_cpu_usec = read_cgroup_cpu_usec();
    struct timespec prev_ts;
    real_clock_gettime(CLOCK_MONOTONIC, &prev_ts);
    double prev_wall = ts_to_d(&prev_ts);

    double integral = 0.0;
    int log_counter = 0;
    int log_every = (int)(2000.0 / interval_ms);  /* log every ~2s */
    if (log_every < 1) log_every = 1;

    for (;;) {
        struct timespec sleep_req;
        sleep_req.tv_sec = interval_ms / 1000;
        sleep_req.tv_nsec = (interval_ms % 1000) * 1000000L;
        real_nanosleep(&sleep_req, NULL);

        long long cur_cpu_usec = read_cgroup_cpu_usec();
        if (cur_cpu_usec < 0) {
            /* cgroup not available — fall back to fixed speed and exit */
            fprintf(stderr, "[warp-pi] cgroup cpu.stat not readable, "
                    "falling back to fixed speed %.1f\n", get_speed());
            return NULL;
        }

        struct timespec cur_ts;
        real_clock_gettime(CLOCK_MONOTONIC, &cur_ts);
        double cur_wall = ts_to_d(&cur_ts);

        double delta_wall_usec = (cur_wall - prev_wall) * 1e6;
        long long delta_cpu_usec = cur_cpu_usec - prev_cpu_usec;

        prev_cpu_usec = cur_cpu_usec;
        prev_wall = cur_wall;

        if (delta_wall_usec < 1000) continue;  /* too short, skip */

        /* CPU utilization: single-CPU model — usage_usec / wall_usec.
         * Capped at 1.0 (single core can't exceed 100%). */
        double cpu_util = (double)delta_cpu_usec / delta_wall_usec;
        if (cpu_util > 1.0) cpu_util = 1.0;
        if (cpu_util < 0.0) cpu_util = 0.0;

        double error = cpu_target - cpu_util;

        /* PI controller */
        integral += error * ((double)interval_ms / 1000.0);

        double output = Kp * error + Ki * integral;

        double cur_speed = get_speed();
        double new_speed = cur_speed + output;

        /* Slew rate limit: prevent jumps larger than max_delta per tick */
        double delta = new_speed - cur_speed;
        if (delta > max_delta) new_speed = cur_speed + max_delta;
        if (delta < -max_delta) new_speed = cur_speed - max_delta;

        /* Clamp to bounds */
        if (new_speed < speed_min) new_speed = speed_min;
        if (new_speed > speed_max) new_speed = speed_max;

        /* Anti-windup: if we're at a bound and the controller wants to push
         * further, undo the integral accumulation to prevent windup */
        if ((new_speed <= speed_min && output < 0) ||
            (new_speed >= speed_max && output > 0)) {
            integral -= error * ((double)interval_ms / 1000.0);
        }

        atomic_store_explicit(&g_speed, new_speed, memory_order_relaxed);
        write_speed_file(new_speed);

        log_counter++;
        if (log_counter >= log_every) {
            log_counter = 0;
            fprintf(stderr, "[warp-pi] speed=%.1f cpu=%.1f%% target=%.0f%% "
                    "err=%.3f int=%.3f out=%.1f\n",
                    new_speed, cpu_util * 100, cpu_target * 100,
                    error, integral, output);
        }
    }
    return NULL;
}

/* ---------- init ---------- */
static atomic_int g_init_lock = 0;

static void ensure_init(void) {
    if (atomic_load_explicit(&g_initialized, memory_order_acquire)) return;

    /* CAS spinlock: only one thread can initialize */
    int expected = 0;
    if (!atomic_compare_exchange_strong(&g_init_lock, &expected, 1)) {
        /* Another thread is initializing — spin until done */
        while (!atomic_load_explicit(&g_initialized, memory_order_acquire)) {}
        return;
    }

    double mono_now = real_monotonic();
    double real_now = real_realtime();

    const char *s = getenv("WARP_SPEED");
    int has_speed = (s && s[0] != '\0');

    if (has_speed) {
        double speed = atof(s);
        if (speed <= 0) speed = 1.0;
        atomic_store_explicit(&g_speed, speed, memory_order_relaxed);
        g_auto_mode = 0;
        fprintf(stderr, "[warp] fixed mode: speed=%.1fx\n", speed);
    } else {
        /* Auto mode: try to inherit speed from the PI controller's shared
         * file (written by the long-running HA process). This lets short-lived
         * `docker exec` processes run at the current warped speed instead of
         * starting at 1.0. If the file doesn't exist, start at 1.0. */
        double inherited = read_speed_file();
        if (inherited > 0) {
            atomic_store_explicit(&g_speed, inherited, memory_order_relaxed);
        } else {
            atomic_store_explicit(&g_speed, 1.0, memory_order_relaxed);
        }
        g_auto_mode = 1;
    }

    const char *a = getenv("WARP_START_REAL");
    g_realtime_anchor = a ? atof(a) : real_now;
    g_mono_anchor = mono_now;
    atomic_store_explicit(&g_initialized, 1, memory_order_release);

    if (g_auto_mode) {
        /* Only spawn the PI controller if no speed file exists yet
         * (i.e., we are the first/main process). Short-lived `docker exec`
         * processes inherit speed from the file and don't need a controller. */
        double inherited = read_speed_file();
        if (inherited <= 0) {
            pthread_t tid;
            pthread_create(&tid, NULL, pi_controller_thread, NULL);
            pthread_detach(tid);
        }
    }
}

static inline double wall_elapsed_since_init(void) {
    return real_monotonic() - g_mono_anchor;
}

static inline double scale_seconds(double d) {
    ensure_init();
    return d / get_speed();
}

static inline int scale_timeout_ms(int timeout_ms) {
    /* -1 = infinite, 0 = non-blocking: leave both unchanged. */
    if (timeout_ms <= 0) return timeout_ms;
    ensure_init();
    double scaled = (double)timeout_ms / get_speed();
    if (scaled < 1.0) return 1;
    if (scaled > (double)INT_MAX) return INT_MAX;
    return (int)scaled;
}

/* ---------- clock reads (virtualize the value) ---------- */
int clock_gettime(clockid_t clk_id, struct timespec *tp) {
    RESOLVE(clock_gettime);
    int r = real_clock_gettime(clk_id, tp);
    if (r != 0 || !tp) return r;
    ensure_init();
    double elapsed = wall_elapsed_since_init();
    if (elapsed < 0) elapsed = 0;
    double virt;
    double spd = get_speed();
    switch (clk_id) {
        case CLOCK_REALTIME:
        case CLOCK_REALTIME_COARSE:
            virt = g_realtime_anchor + elapsed * spd;
            break;
        case CLOCK_MONOTONIC:
        case CLOCK_MONOTONIC_RAW:
        case CLOCK_MONOTONIC_COARSE:
        case CLOCK_BOOTTIME:
            virt = g_mono_anchor + elapsed * spd;
            break;
        default:
            return 0; /* leave unknown clocks alone */
    }
    d_to_ts(virt, tp);
    return 0;
}

int gettimeofday(struct timeval *tv, void *tz) {
    RESOLVE(gettimeofday);
    int r = real_gettimeofday(tv, tz);
    if (r != 0 || !tv) return r;
    ensure_init();
    double virt = g_realtime_anchor + wall_elapsed_since_init() * get_speed();
    if (virt < 0) virt = 0;
    tv->tv_sec = (time_t)virt;
    tv->tv_usec = (suseconds_t)((virt - (double)tv->tv_sec) * 1e6);
    return 0;
}

time_t time(time_t *t) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    if (t) *t = ts.tv_sec;
    return ts.tv_sec;
}

/* ---------- sleeps / waits (scale the timeout) ---------- */
int nanosleep(const struct timespec *req, struct timespec *rem) {
    RESOLVE(nanosleep);
    if (!req) {
        errno = EFAULT;
        return -1;
    }
    double d = ts_to_d(req);
    if (d <= 0) return real_nanosleep(req, rem);
    double scaled = scale_seconds(d);
    struct timespec s;
    d_to_ts(scaled, &s);
    return real_nanosleep(&s, rem);
}

int clock_nanosleep(clockid_t clk, int flags, const struct timespec *req, struct timespec *rem) {
    RESOLVE(clock_nanosleep);
    if (!req) return EINVAL;
    if (flags & TIMER_ABSTIME) {
        /* Absolute deadline: requested in virtual time. Translate to real-time
         * deadline and call with TIMER_ABSTIME. */
        double virt_deadline = ts_to_d(req);
        double virt_now;
        struct timespec now_ts;
        clock_gettime(clk, &now_ts);
        virt_now = ts_to_d(&now_ts);
        double virt_wait = virt_deadline - virt_now;
        if (virt_wait <= 0) return 0;
        double real_wait = virt_wait / get_speed();
        /* Switch to relative wait — simpler & avoids translating clock domains. */
        struct timespec rel;
        d_to_ts(real_wait, &rel);
        return real_clock_nanosleep(clk, 0, &rel, rem);
    }
    double d = ts_to_d(req);
    if (d <= 0) return real_clock_nanosleep(clk, flags, req, rem);
    double scaled = scale_seconds(d);
    struct timespec s;
    d_to_ts(scaled, &s);
    return real_clock_nanosleep(clk, flags, &s, rem);
}

int epoll_wait(int epfd, struct epoll_event *events, int maxevents, int timeout) {
    RESOLVE(epoll_wait);
    return real_epoll_wait(epfd, events, maxevents, scale_timeout_ms(timeout));
}

int epoll_pwait(int epfd, struct epoll_event *events, int maxevents, int timeout, const sigset_t *sigmask) {
    RESOLVE(epoll_pwait);
    return real_epoll_pwait(epfd, events, maxevents, scale_timeout_ms(timeout), sigmask);
}

int poll(struct pollfd *fds, nfds_t nfds, int timeout) {
    RESOLVE(poll);
    return real_poll(fds, nfds, scale_timeout_ms(timeout));
}

int ppoll(struct pollfd *fds, nfds_t nfds, const struct timespec *tmo, const sigset_t *sigmask) {
    RESOLVE(ppoll);
    if (!tmo) return real_ppoll(fds, nfds, NULL, sigmask);
    double d = ts_to_d(tmo);
    if (d <= 0) return real_ppoll(fds, nfds, tmo, sigmask);
    double scaled = scale_seconds(d);
    struct timespec s;
    d_to_ts(scaled, &s);
    return real_ppoll(fds, nfds, &s, sigmask);
}

int select(int nfds, fd_set *r, fd_set *w, fd_set *e, struct timeval *tv) {
    RESOLVE(select);
    if (!tv) return real_select(nfds, r, w, e, tv);
    double d = (double)tv->tv_sec + (double)tv->tv_usec / 1e6;
    if (d <= 0) return real_select(nfds, r, w, e, tv);
    double scaled = scale_seconds(d);
    struct timeval s;
    s.tv_sec = (time_t)scaled;
    s.tv_usec = (suseconds_t)((scaled - (double)s.tv_sec) * 1e6);
    return real_select(nfds, r, w, e, &s);
}

int pselect(int nfds, fd_set *r, fd_set *w, fd_set *e, const struct timespec *tmo, const sigset_t *sigmask) {
    RESOLVE(pselect6);
    if (!tmo) return real_pselect6(nfds, r, w, e, tmo, sigmask);
    double d = ts_to_d(tmo);
    if (d <= 0) return real_pselect6(nfds, r, w, e, tmo, sigmask);
    double scaled = scale_seconds(d);
    struct timespec s;
    d_to_ts(scaled, &s);
    return real_pselect6(nfds, r, w, e, &s, sigmask);
}
