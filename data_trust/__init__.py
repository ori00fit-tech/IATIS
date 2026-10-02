"""
data_trust/
--------------
Hypothesis Discovery Engine, Phase 11 — Data Trust & Provenance Layer.

A trust-assessment layer OVER the existing, already-proven closed-bar
and OHLCV validation controls (core.data_validator.validate_ohlcv(),
core.data_providers._drop_still_forming_bar(), core.timeframe_sync.
resample()) -- never a second, competing data-fetch/reconstruction
layer. Nothing here fetches, resamples, or trims data; it only assesses
and fingerprints a DataFrame a caller already obtained through the
existing, unmodified pipeline.

Pure and storage-free (operator's own locked decision): every function
in this package returns a plain dict with no side effects, no I/O, and
no database writes. Persisting a manifest anywhere is explicitly a
LATER phase's decision, once a real consumer and its retention needs
are known -- inventing that now would be exactly the premature
complexity this engine's own discipline has consistently refused.
"""
