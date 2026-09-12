"""Small-sample summaries: slower observed case, not a population percentile."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence


def summarize(values: Sequence[float]) -> dict[str, int | float]:
    if not values or any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Supply finite, nonnegative turn timings.")
    return {
        "turns": len(values),
        "median_seconds": round(statistics.median(values), 3),
        "slowest_seconds": round(max(values), 3),
        "fastest_seconds": round(min(values), 3),
    }
