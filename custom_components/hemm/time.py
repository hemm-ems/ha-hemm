"""HA-side `Clock` implementation.

The integration must read time exclusively through this `HAClock` so that
the time-warp test harness can substitute a `VirtualClock` from the core
library and drive HEMM at simulated time. The audit script
(`tools/check_clock.py`) forbids direct `dt_util.utcnow()` / `dt_util.now()`
/ `time.monotonic()` calls outside this module.
"""

from __future__ import annotations

import time as _time
from datetime import datetime

from homeassistant.util import dt as dt_util


class HAClock:
    """`Clock` wrapper backed by HA's `dt_util`.

    `now()` returns whatever `dt_util.utcnow()` returns — so monkey-patching
    `homeassistant.util.dt.utcnow` (the standard pytest-homeassistant pattern)
    automatically virtualizes every time read in the integration.
    """

    def now(self) -> datetime:
        return dt_util.utcnow()

    def monotonic(self) -> float:
        # Wall-clock monotonic is fine here: it is used only for elapsed-time
        # metrics, never for business logic. The Phase B/C time-warp engine
        # drives the integration via `dt_util` patching and explicit ticks,
        # so the monotonic value's drift relative to virtual `now()` is
        # benign (it just shows that solver work consumed wall time, which
        # is true).
        return _time.monotonic()
