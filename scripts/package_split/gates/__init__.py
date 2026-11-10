"""Gates for the package-split harness.

Each gate module exposes ``run_gate(manifest, args, report)`` and mutates the
report with its checks. The CLI never calls gates directly; it goes through
``REGISTRY`` so that the set of runnable gates is defined in exactly one
place.

This repo registers the source-side freeze gate first (freeze-first: the
frozen baseline is established on main before any extraction lands). The
remaining gates live on the package-split branches until they are reviewed
in separately.
"""

from __future__ import annotations

from . import freeze


REGISTRY = {
    "freeze": freeze.run_gate,
}
