"""imagegen — static invariant-linter for base-image Docker images.

Scope and rules: see docs/invariants.md (verified) and docs/adr/0001.
The linter is a FAST gate, not a correctness gate — the real `docker build`
(+ smoke test) is the correctness check.
"""
