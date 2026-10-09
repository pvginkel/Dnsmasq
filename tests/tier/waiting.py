import time
from collections.abc import Callable


def wait_for[T](
    probe: Callable[[], T | None], timeout: float, what: str, interval: float = 0.2
) -> T:
    """Polls `probe` until it returns something other than None, and returns that."""
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value is not None:
            return value
        if time.monotonic() > deadline:
            raise TimeoutError(f"gave up after {timeout:.0f}s waiting for {what}")
        time.sleep(interval)
