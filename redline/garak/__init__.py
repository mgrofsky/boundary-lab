"""Use Redline with garak: grade a garak report, or run Redline as a garak detector.

Nothing here imports garak except detector.py and probe.py, and those are only
imported by garak itself, through the shim files that redline garak-install writes.
"""

from redline.garak.report import STOP_REASONS, estimate_report_calls, grade_report

__all__ = ["STOP_REASONS", "estimate_report_calls", "grade_report"]
