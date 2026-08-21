"""Compatibility entry point for Smart v1.2.5 A-bounded validation.

The candidate graduated from shadow-only validation into the production
``lyric_aligner.timeline.a_bounded_reconcile`` module after public synthetic CI
and a byte-identical private clean rerun.  Keep these aliases so the original
validation command/import remains reproducible without maintaining a second
implementation.
"""

from lyric_aligner.timeline.a_bounded_reconcile import (
    ABoundedRecoverySummary as ABoundedShadowSummary,
    recover_mapped_reviews_from_a_bounded as recover_mapped_reviews_from_a_bounded_shadow,
)

__all__ = [
    "ABoundedShadowSummary",
    "recover_mapped_reviews_from_a_bounded_shadow",
]
