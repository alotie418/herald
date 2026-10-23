"""Sleep between requests so a check does not burst a host."""

from __future__ import annotations

import time
from collections.abc import Callable


class HostLimiter:
    """Per-host interval plus a gap between any two requests.

    ``floors`` can force a slower host (JavDB) without slowing every site down
    to that same interval. The gap and the host interval overlap; they do not
    stack.
    """

    def __init__(
        self,
        default_interval: float,
        global_gap: float,
        floors: dict[str, float] | None = None,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.default_interval = default_interval
        self.global_gap = global_gap
        self.floors = floors or {}
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last_any: float | None = None
        self._last_host: dict[str, float] = {}

    def interval_for(self, host: str) -> float:
        floor = 0.0
        lowered = host.lower()
        for suffix, seconds in self.floors.items():
            suffix = suffix.lower()
            if lowered == suffix or lowered.endswith("." + suffix):
                floor = max(floor, seconds)
        return max(self.default_interval, floor)

    def wait(self, host: str) -> None:
        now = self._clock()
        delays = [0.0]
        if self._last_any is not None:
            delays.append(self.global_gap - (now - self._last_any))
        previous = self._last_host.get(host)
        if previous is not None:
            delays.append(self.interval_for(host) - (now - previous))
        delay = max(delays)
        if delay > 0:
            self._sleep(delay)
        stamp = self._clock()
        self._last_any = stamp
        self._last_host[host] = stamp
