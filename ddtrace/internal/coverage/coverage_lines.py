"""Bridge to the extracted ``ddtrace-coverage`` package: this module *is* ``_ddtrace_coverage.coverage_lines``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_coverage.coverage_lines as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
