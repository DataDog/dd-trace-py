import contextlib

from zeep import Client
from zeep.transports import Transport

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
        yield TracerSpanContainer(tracer)


@contextlib.contextmanager
def with_default_django_db(test_spans):
    from django.core.management import call_command
    from django.db import connections

    # Run migrations on the default database (SQLite :memory:)
    # No need for setup_databases/teardown_databases since we're using :memory:
    call_command("migrate", verbosity=0, interactive=False)
    # Clear the migration spans
    test_spans.reset()
    try:
        yield
    finally:
        # Close all database connections
        connections.close_all()
