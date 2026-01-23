import contextlib

from zeep import Client
from zeep.transports import Transport

from ddtrace.internal.settings._config import config
from tests.utils import TracerSpanContainer
from tests.utils import scoped_tracer


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


@contextlib.contextmanager
def setup_django_test_spans():
    setup_django()

    with scoped_tracer() as tracer:
        config.django._tracer = tracer
        yield TracerSpanContainer(config.django._tracer)


@contextlib.contextmanager
def with_django_db(test_spans):
    from django.db import connections
    from django.test.utils import setup_databases
    from django.test.utils import teardown_databases

    old_config = setup_databases(
        verbosity=0,
        interactive=False,
        keepdb=True,  # Reuse existing test databases to handle interrupted runs
    )
    # Clear the migration spans
    test_spans.reset()
    try:
        yield
    finally:
        # Close all database connections before teardown.
        # PostgreSQL won't drop a database if there are open connections to it.
        connections.close_all()
        teardown_databases(old_config, verbosity=0)
