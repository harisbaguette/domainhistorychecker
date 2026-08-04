"""Global adaptive rate limiter for the Wayback Machine.

Wayback throttles per IP (observed 15~60 req/min; repeated 429 bursts lead to an
hour-long block). Start at 30/min, and the first 429 drops the whole process to
12/min — below the observed floor — plus an exponential backoff pause.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

START_RPM = 30
DEGRADED_RPM = 12
BACKOFF_BASE = 3.0
BACKOFF_MAX = 300.0


class AdaptiveRateLimiter:
    """Serialises calls to a shared upstream with an adaptive minimum interval."""

    def __init__(
        self,
        rpm: int = START_RPM,
        degraded_rpm: int = DEGRADED_RPM,
        backoff_base: float = BACKOFF_BASE,
        backoff_max: float = BACKOFF_MAX,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.rpm = max(1, rpm)
        self.degraded_rpm = max(1, degraded_rpm)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.backoff = backoff_base
        self.degraded = False
        self.hits_429 = 0
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def interval(self) -> float:
        return 60.0 / self.rpm

    async def acquire(self) -> None:
        """Wait until the next slot is free, then reserve the following one."""
        async with self._lock:
            now = self._clock()
            wait = self._next_at - now
            if wait > 0:
                await self._sleeper(wait)
                now = self._clock()
            self._next_at = max(now, self._next_at) + self.interval

    def note_429(self) -> None:
        """Throttle everything down and push the next slot back by the backoff."""
        self.hits_429 += 1
        if not self.degraded:
            self.degraded = True
            self.rpm = self.degraded_rpm
        now = self._clock()
        self._next_at = max(now, self._next_at) + self.backoff
        self.backoff = min(self.backoff * 2, self.backoff_max)

    def note_success(self) -> None:
        """Reset the backoff step; the degraded rate is kept for the whole run."""
        self.backoff = self.backoff_base
