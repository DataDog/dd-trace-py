"""Gates for the package-split harness.

Each gate module exposes ``run_gate(manifest, args, report)`` and mutates the
report with its checks. The CLI never calls gates directly; it goes through
``REGISTRY`` so that the set of runnable gates is defined in exactly one
place. Gate implementations register themselves here as they land in their
own PRs (the freeze gate is the first, in the stacked PR #21709).
"""

from __future__ import annotations

from collections.abc import Callable


REGISTRY: dict[str, Callable[..., object]] = {}
