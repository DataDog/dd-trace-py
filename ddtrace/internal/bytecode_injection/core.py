"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.bytecode_injection.core``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.bytecode_injection.core as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.bytecode_injection.core import ABSOLUTE_JUMPS as ABSOLUTE_JUMPS  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import BACKWARD_JUMPS as BACKWARD_JUMPS  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import CALL as CALL  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import EXTENDED_ARG as EXTENDED_ARG  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import FORWARD_JUMPS as FORWARD_JUMPS  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import IMPORT_FROM as IMPORT_FROM  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import IMPORT_NAME as IMPORT_NAME  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import JUMPS as JUMPS  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import LOAD_CONST as LOAD_CONST  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import POP_TOP as POP_TOP  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import CallbackType as CallbackType  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import InjectionContext as InjectionContext  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import _consume_signed_varint as _consume_signed_varint  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import _consume_varint as _consume_varint  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import (
        _generate_adjusted_location_data as _generate_adjusted_location_data,
    )  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import (
        _generate_adjusted_location_data_3_10 as _generate_adjusted_location_data_3_10,
    )  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import (
        _generate_adjusted_location_data_3_11 as _generate_adjusted_location_data_3_11,
    )  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import (
        _generate_exception_table as _generate_exception_table,  # noqa: F401
    )
    from _ddtrace_internal.bytecode_injection.core import (
        _inject_invocation_nonrecursive as _inject_invocation_nonrecursive,
    )  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import inject_invocation as inject_invocation  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import is_python_3_10 as is_python_3_10  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import is_python_3_11 as is_python_3_11  # noqa: F401
    from _ddtrace_internal.bytecode_injection.core import to_varint as to_varint  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
