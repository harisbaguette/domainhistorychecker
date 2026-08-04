import pytest

from domainchecker.ratelimit import AdaptiveRateLimiter


class FakeClock:
    """Virtual time so the tests never actually sleep."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


async def test_starts_at_30_per_minute(clock):
    limiter = AdaptiveRateLimiter(clock=clock, sleeper=clock.sleep)
    assert limiter.interval == pytest.approx(2.0)
    await limiter.acquire()
    await limiter.acquire()
    assert clock.slept == [pytest.approx(2.0)]


async def test_429_drops_to_12_per_minute_and_backs_off(clock):
    limiter = AdaptiveRateLimiter(clock=clock, sleeper=clock.sleep)
    await limiter.acquire()
    limiter.note_429()

    assert limiter.rpm == 12
    assert limiter.interval == pytest.approx(5.0)
    assert limiter.degraded is True

    await limiter.acquire()  # backoff 3s + 이미 예약된 간격만큼 대기
    assert clock.slept[-1] >= 3.0


async def test_backoff_is_exponential_and_capped(clock):
    limiter = AdaptiveRateLimiter(backoff_max=12.0, clock=clock, sleeper=clock.sleep)
    steps = []
    for _ in range(5):
        steps.append(limiter.backoff)
        limiter.note_429()
    assert steps[:3] == [3.0, 6.0, 12.0]
    assert limiter.backoff == 12.0  # capped
    assert limiter.hits_429 == 5


async def test_success_resets_backoff_but_keeps_degraded_rate(clock):
    limiter = AdaptiveRateLimiter(clock=clock, sleeper=clock.sleep)
    limiter.note_429()
    limiter.note_429()
    limiter.note_success()
    assert limiter.backoff == 3.0
    assert limiter.rpm == 12  # 한 번 내려간 속도는 그 실행 동안 유지
