"""Empirical return distributions; no learned quantile-regression claim."""

import math
from statistics import mean


def quantile(samples, q):
    ordered = sorted(samples)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def score(samples, config):
    if not samples:
        return float("-inf")
    if config.objective == "lower_tail":
        return mean(sorted(samples)[: max(1, math.ceil(len(samples) * config.tail_fraction))])
    return mean(samples)


def summary(samples):
    if not samples:
        return {"samples": 0}
    return {
        "samples": len(samples),
        "mean": mean(samples),
        "p10": quantile(samples, 0.1),
        "p50": quantile(samples, 0.5),
        "p90": quantile(samples, 0.9),
        "worst": min(samples),
    }
