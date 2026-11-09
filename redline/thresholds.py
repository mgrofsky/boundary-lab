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
) -> list[str]:
    """Return one plain line for each limit the summary breaks. An empty list means it passes.

    Recall is the share of rule breaks caught. The false alarm rate is the share of good
    answers marked fail. Kappa is agreement with the hand labels. With attack set, the
    attack run's numbers are checked instead of the holdout's.
    """
    where = "attack answers" if attack else "holdout"
    paths = PATHS[attack]
    limits = [
        ("recall", min_recall, "below the minimum"),
        ("false alarm rate", max_false_alarm, "above the maximum"),
        ("kappa", min_kappa, "below the minimum"),
    ]
    lines = []
    for name, limit, side in limits:
        if limit is None:
            continue
        value = _value(summary, paths[name])
        broken = value < limit if side == "below the minimum" else value > limit
        if broken:
            lines.append(f"{name} on {where} is {value:.3f}, {side} of {limit:.3f}")
    return lines
