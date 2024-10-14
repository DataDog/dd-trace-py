import sys
import pytest
from ddtrace.internal.error_reporting.handled_exceptions import HandledExceptionReportingInjector

skipif_bytecode_injection_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 10),
    reason="Injection is only supported for 3.10+",
)


@skipif_bytecode_injection_not_supported
def test_import_module():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    from tests.internal.error_reporting.sample_module_to_instrument import a_function_at_the_root_level, AClass
    injector = HandledExceptionReportingInjector(['tests.internal.error_reporting'], callback=callback)
    injector.instrument_module_conditionally('tests.internal.error_reporting.sample_module_to_instrument')

    value = ''
    assert a_function_at_the_root_level() == '<try_root><except_root>'
    assert value == '<error_function_root>'

    value = ''
    assert AClass().instance_method() == '<try_method><except_method>'
    assert value == '<error_method>'

    value = ''
    assert AClass.static_method() == '<try_static><except_static>'
    assert value == '<error_static>'
