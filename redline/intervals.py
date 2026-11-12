"""How sure a share can be, given how few answers it was counted from."""

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Return the Wilson score interval (low, high) for k successes out of n.

    z of 1.96 gives a 95 percent interval. The ends are kept between 0 and 1. With no
    trials at all nothing is known, so the interval is the whole range.
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    zz = z * z
    denom = 1 + zz / n
    center = (p + zz / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + zz / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def interval(k: int, n: int) -> dict:
    """The count behind a share and its 95 percent interval, as stored in summary.json."""
    low, high = wilson(k, n)
    return {"k": k, "n": n, "ci95": [low, high]}
