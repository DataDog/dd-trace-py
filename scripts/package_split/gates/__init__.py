"""Gates for the package-split harness.

Each gate module exposes ``run_gate(manifest, args, report)`` and mutates the
report with its checks. The CLI never calls gates directly; it goes through
``REGISTRY`` so that the set of runnable gates is defined in exactly one
place.

This repo registers the source-side freeze gate (freeze-first: the frozen
baseline is established on main before any extraction lands). Additional
gates register here as they land in their own PRs.
"""

from __future__ import annotations

from . import freeze


REGISTRY = {
    "freeze": freeze.run_gate,
}
