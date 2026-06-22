"""Smart Money signal engine — the 'Oracle'.

Implements the deterministic, pure signal stack from docs/mechanical-signal-system-spec.md:
  metrics (S-A) -> atomic signals (S-B) -> layer engines (S-C) -> reconciliation (S-D).

The only public entry point is engine.compute_stance(...).
"""
