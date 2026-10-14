"""Legacy re-export shim for a moved module.

``ddtrace.testing.internal.test_data`` moved to:

``ddtrace_testing.internal.test_data``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_testing.internal.test_data import ModuleRef as ModuleRef  # noqa: F401
from ddtrace_testing.internal.test_data import SuiteRef as SuiteRef  # noqa: F401
from ddtrace_testing.internal.test_data import TChildClass as TChildClass  # noqa: F401
from ddtrace_testing.internal.test_data import Test as Test  # noqa: F401
from ddtrace_testing.internal.test_data import TestItem as TestItem  # noqa: F401
from ddtrace_testing.internal.test_data import TestModule as TestModule  # noqa: F401
from ddtrace_testing.internal.test_data import TestRef as TestRef  # noqa: F401
from ddtrace_testing.internal.test_data import TestRun as TestRun  # noqa: F401
from ddtrace_testing.internal.test_data import TestSession as TestSession  # noqa: F401
from ddtrace_testing.internal.test_data import TestStatus as TestStatus  # noqa: F401
from ddtrace_testing.internal.test_data import TestSuite as TestSuite  # noqa: F401
from ddtrace_testing.internal.test_data import TestTag as TestTag  # noqa: F401
from ddtrace_testing.internal.test_data import TestType as TestType  # noqa: F401
from ddtrace_testing.internal.test_data import TParentClass as TParentClass  # noqa: F401
