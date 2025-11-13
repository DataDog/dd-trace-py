import contextlib

from zeep import Client
from zeep.transports import Transport


def make_soap_request(url):
    client = Client(wsdl=url, transport=Transport())

    # Call the SOAP service
    response = client.service.EmployeeLeaveStatus(LeaveID="124", Description="Annual leave")
    # Print the response
    print(f"Success: {response.success}")
    print(f"ErrorText: {response.errorText}")

    return response


def setup_django():
    import django

    from ddtrace.contrib.internal.django.patch import patch

    patch()
    django.setup()


def setup_django_test_spans():
    setup_django()

    from ddtrace.internal.settings._config import config
    from tests.utils import DummyTracer
    from tests.utils import TracerSpanContainer

    config.django._tracer = DummyTracer()
    return TracerSpanContainer(config.django._tracer)


@contextlib.contextmanager
def with_django_db(test_spans=None):
    from django.test.utils import setup_databases
    from django.test.utils import teardown_databases

    old_config = setup_databases(
        verbosity=0,
        interactive=False,
        keepdb=False,
    )
    if test_spans is not None:
        # Clear the migration spans
        test_spans.reset()
    try:
        yield
    finally:
        teardown_databases(old_config, verbosity=0)
