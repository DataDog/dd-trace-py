"""Gates for the package-split harness.

Each gate module exposes ``run_gate(manifest, args, report)`` and mutates the
report with its checks. The CLI never calls gates directly; it goes through
``REGISTRY`` so that the set of runnable gates is defined in exactly one
place.
"""

from __future__ import annotations

from . import artifacts
from . import boundaries
from . import compatibility
from . import contracts
from . import freeze
from . import lifecycle
from . import migration


REGISTRY = {
    "boundaries": boundaries.run_gate,
    "contracts": contracts.run_gate,
    "artifacts": artifacts.run_gate,
    "lifecycle": lifecycle.run_gate,
    "compatibility": compatibility.run_gate,
    "migration": migration.run_gate,
    "freeze": freeze.run_gate,
}
