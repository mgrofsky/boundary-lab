"""Compare the numbers in summary.json with limits you set, for use as a CI check."""

# Where each number lives in summary.json, for the holdout run and the attack run.
PATHS = {
    False: {
        "recall": ("end_to_end", "bad", "caught"),
        "false alarm rate": ("end_to_end", "good", "wrongly_failed"),
        "kappa": ("judge", "holdout", "cohen_kappa"),
    },
    True: {
        "recall": ("attack_end_to_end", "bad", "caught"),
        "false alarm rate": ("attack_end_to_end", "good", "wrongly_failed"),
        "kappa": ("judge", "attack", "cohen_kappa"),
    },
}


class NoCounts(ValueError):
    """The summary has no counts, so an interval can't be tested."""


def _counts(summary: dict, path: tuple[str, ...]) -> dict:
    """The count and interval stored beside a rate, under the block's "counts" key."""
    block = summary[path[0]]
    try:
        return block["counts"][path[1]][path[2]]
    except KeyError:
        raise NoCounts(
            f"the summary has no counts for {'.'.join(path)}, so --use-interval can't be "
            "checked. Run redline report again with Redline 0.3.0 or later."
        ) from None


def _value(summary: dict, path: tuple[str, ...]) -> float:
    for key in path:
        summary = summary[key]
    return float(summary)


def check(
    summary: dict,
    min_recall: float | None = None,
    max_false_alarm: float | None = None,
    min_kappa: float | None = None,
    attack: bool = False,
    use_interval: bool = False,
) -> list[str]:
    """Return one plain line for each limit the summary breaks. An empty list means it passes.

    Recall is the share of rule breaks caught. The false alarm rate is the share of good
    answers marked fail. Kappa is agreement with the hand labels. With attack set, the
    attack run's numbers are checked instead of the holdout's.

    With use_interval set, recall is tested on the low end of its 95 percent interval and
    the false alarm rate on the high end. Kappa is tested as before. A summary with no
    counts raises NoCounts.
    """
    where = "attack answers" if attack else "holdout"
    paths = PATHS[attack]
    limits = [
        ("recall", min_recall, "below the minimum"),
        ("false alarm rate", max_false_alarm, "above the maximum"),
        ("kappa", min_kappa, "below the minimum"),
    ]
    if use_interval:
        for name in ("recall", "false alarm rate"):
            _counts(summary, paths[name])
    lines = []
    for name, limit, side in limits:
        if limit is None:
            continue
        value = _value(summary, paths[name])
        below = side == "below the minimum"
        if use_interval and name != "kappa":
            low, high = _counts(summary, paths[name])["ci95"]
            tested = low if below else high
            if tested < limit if below else tested > limit:
                end = "low" if below else "high"
                lines.append(
                    f"{name} on {where} is {value:.3f} (95% interval {low:.3f} to {high:.3f}), "
                    f"its {end} end is {side} of {limit:.3f}"
                )
            continue
        broken = value < limit if below else value > limit
        if broken:
            lines.append(f"{name} on {where} is {value:.3f}, {side} of {limit:.3f}")
    return lines
