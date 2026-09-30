"""Event records and paired ROI reporting."""

from .comparison import compare_paired_runs, write_comparison
from .events import EventLogger

__all__ = ["EventLogger", "compare_paired_runs", "write_comparison"]
