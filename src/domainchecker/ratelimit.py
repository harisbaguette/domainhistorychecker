"""Global adaptive rate limiter for the Wayback Machine.

Wayback throttles per IP (observed 15~60 req/min; repeated 429 bursts lead to an
hour-long block). Start at 30/min, and the first 429 drops the whole process to
12/min — below the observed floor — plus an exponential backoff pause.

막힐 때마다 더 세게 물러선다: 429를 또 받으면 남은 속도를 절반으로 접어(바닥 4/분)
계속 두드리다 한 시간 차단당하는 길을 막는다. 그리고 성공이 계속 쌓이면 아주
조금씩만 올라오되, 처음의 30/분까지 돌아가지 않고 **오래 두드려도 안전한 선**
(15/분)에서 멈춘다 — 실측상 분당 60은 잠깐만 되는 한계이고, 계속 가도 되는 선은
분당 12~15다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

START_RPM = 30
DEGRADED_RPM = 12
FLOOR_RPM = 4  # 아무리 많이 막혀도 이보다 더 느려지지는 않는다
RECOVER_RPM = 15  # 회복해도 여기까지 — 계속 두드려도 되는 선
RECOVER_AFTER = 10  # 연속 성공 몇 번마다 한 칸 올릴지
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
        floor_rpm: int = FLOOR_RPM,
        recover_rpm: int = RECOVER_RPM,
        recover_after: int = RECOVER_AFTER,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.rpm = max(1, rpm)
        self.degraded_rpm = max(1, degraded_rpm)
        self.floor_rpm = max(1, floor_rpm)
        self.recover_rpm = max(1, recover_rpm)
        self.recover_after = max(1, recover_after)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.backoff = backoff_base
        self.degraded = False
        self.hits_429 = 0
        self.streak = 0  # 막히지 않고 이어 간 성공 수(회복 속도를 재는 자)
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
        """Throttle everything down and push the next slot back by the backoff.

        처음 막히면 곧장 12/분으로 떨어지고, 또 막히면 그때마다 남은 속도를
        절반으로 접는다(바닥 4/분). 같은 속도로 계속 두드려서 한 시간짜리
        차단을 부르는 일을 막는 장치다.
        """
        self.hits_429 += 1
        self.streak = 0
        if not self.degraded:
            self.degraded = True
            self.rpm = self.degraded_rpm
        elif self.hits_429 >= 3:
            # 두 번째까지는 아까 걸린 벌이 아직 안 풀린 것일 수 있어 기다림만 늘린다.
            # 세 번째부터는 12/분도 빠르다는 뜻이므로 남은 속도를 절반으로 접는다.
            self.rpm = max(self.floor_rpm, self.rpm // 2)
        now = self._clock()
        self._next_at = max(now, self._next_at) + self.backoff
        self.backoff = min(self.backoff * 2, self.backoff_max)

    def note_success(self) -> None:
        """Reset the backoff step and creep back up — never past the safe line.

        성공이 연달아 쌓일 때만 한 칸씩 올라간다. 30/분으로는 돌아가지 않는다 —
        오래 두드려도 되는 선은 분당 15까지다.
        """
        self.backoff = self.backoff_base
        if not self.degraded:
            return
        self.streak += 1
        if self.streak >= self.recover_after and self.rpm < self.recover_rpm:
            self.streak = 0
            self.rpm = min(self.recover_rpm, self.rpm + max(1, self.rpm // 4))
