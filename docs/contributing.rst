==============
 Contributing
==============

When contributing to this repository, we advise you to discuss the change you
wish to make via an `issue <https://github.com/DataDog/dd-trace-py/issues>`_.


Branches
========

Development happens in the `1.x` branch. When all the features for the next
milestone are merged, the next version is released and tagged on the `1.x`
branch as `vVERSION`.

Your pull request should target the `1.x` branch.

Once a new version is released, a `VERSION` branch might be created to
support micro releases to `VERSION`. Patches should be cherry-picking from the
`1.x` branch where possible — or otherwise created from scratch.


Internal API
============

The `ddtrace.internal` module contains code that must only be used inside
`ddtrace` itself. Relying on the API of this module is dangerous and can break
at anytime. Don't do it.

Python Versions and Implementations Support
===========================================

The following Python implementations are supported:

- CPython

Versions of those implementations that are supported are the Python versions
that are currently supported by the community.

Code Style
==========

The code style is enforced by `flake8 <https://pypi.org/project/flake8>`_, its
configuration, and possibly extensions. No code style review should be done by
a human. All code style enforcement must be automated to avoid bikeshedding
and losing time.


How To: Write an Integration
============================

An integration should provide concise, insightful data about the library or
framework that will aid developers in monitoring their application's health and
performance.

The best way to get started writing a new integration is to refer to existing
integrations. Looking at a similarly themed library or framework is a great
starting point. To write a new integration for ``memcached`` we might refer to
the existing ``redis`` integration as a starting point since both of these
would generate similar spans.

The development process looks like this:

  - Research the library or framework that is to be instrumented. Reading
    through its docs and code examples will reveal what APIs are meaningful to
    instrument.

  - Copy the skeleton module provided in ``templates/integration`` and replace
    ``foo`` with the integration name. The integration name typically matches
    the library or framework being instrumented::

      cp -r templates/integration ddtrace/contrib/<integration>

  - Create a test file for the integration under
    ``tests/contrib/<integration>/test_<integration>.py``.

  - Write the integration (see more on this below).

  - Open up a draft PR using the `integration checklist
    <https://github.com/DataDog/dd-trace-py/.github/PULL_REQUEST_TEMPLATE/integration.md>`_.


Integration Fundamentals
++++++++++++++++++++++++

Code structure
~~~~~~~~~~~~~~

All integrations live in ``ddtrace/contrib/`` and contain at least two files,
``__init__.py`` and ``patch.py``. A skeleton integration is available under
``templates/integration`` which can be used as a starting point::

    cp -r templates/integration ddtrace/contrib/<integration>


It is preferred to keep as much code as possible in ``patch.py``.


Pin API
~~~~~~~

The Pin API is used to configure the instrumentation at run-time. This includes
enabling and disabling the instrumentation and overriding the service name.


Library support
~~~~~~~~~~~~~~~

``ddtrace`` tries to support as many active versions of a library as possible.
The general rule is:

  - If the integration depends on internals of the library then test every
    minor version going back 2 years.

  - Else test each major version going back 2 years.


For libraries with many versions it is recommended to pull out the version of
the library to use when instrumenting volatile features. A great example of
this is the Flask integration:

    - pulling out the version: `flask version <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L45-L58>`_
    - using it to instrument a later-added feature `flask version usage <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L149-L151>`_


Exceptions/Errors
~~~~~~~~~~~~~~~~~

Exceptions provide a lot of useful information about errors and the application
as a whole and are fortunately usually quite easy to deal with. Exceptions are
a great place to start instrumenting. There are a couple of considerations when
dealing with exceptions in ``ddtrace``:

    - Re-raising the exception: it is crucial that we do not interfere with the
      application, so exceptions must be re-raised. See the `bottle exception handling <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/bottle/trace.py#L50-L69>`_
      instrumentation for an example.

    - Gathering relevant information: ``ddtrace`` provides a helper for pulling
      out this information and adding it to a span.  See the `cassandra
      exception handling
      <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/cassandra/session.py#L117-L122>`_
      instrumentation for an example.


Cross execution tracing
~~~~~~~~~~~~~~~~~~~~~~~

Some integrations can propagate a trace across execution boundaries to other
executions where the trace is continued (processes, threads, tasks, etc). Refer
to the :ref:`context` section of the documentation for more information.

    - Propagating the trace example: `requests <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/requests/connection.py#L95-L97>`_
    - Receiving and activating a propagated trace example: `django <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/django/patch.py#L304>`__


Web frameworks
++++++++++++++


A web framework integration must do the following if possible:

    - Install the WSGI or ASGI trace middlewares already provided by ``ddtrace``.
    - Trace the duration of the request.
    - Assign a resource name for a route.
    - Use ``trace_utils.set_http_meta`` to set the standard http tags.
    - Have an internal service name.
    - Support distributed tracing (configurable).
    - Provide insight to middlewares and views.
    - Use the `SpanTypes.WEB` span type.

Some example web framework integrations::
    - `flask <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/flask>`_
    - `django <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/django>`__


Database libraries
++++++++++++++++++

``ddtrace`` already provides base instrumentation for the Python database API
(PEP 249) which most database client libraries implement in the
`ddtrace.contrib.dbapi <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/dbapi/__init__.py>`_
module.

Check out some of our existing database integrations for how to use the `dbapi`:

    - `mariadb <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/mariadb>`_
    - `psycopg <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/psycopg>`_
    - `mysql <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/mysql>`_


Testing
+++++++

The tests must be defined in its own module in ``tests/contrib/<integration>/``.

Testing is the most important part of the integration. We have to be certain
that the integration:

    1) works: submits meaningful information to Datadog

    2) is invisible: does not impact the library or application by disturbing state,
       performance or causing errors

The best way to get started writing tests is to reference other integration test
suites. ``tests/contrib/django`` and ``tests/contrib/mariadb`` are good examples.
Be sure to make use of the test utilities and fixtures which will make testing
less of a burden.


Trace Examples
++++++++++++++

Optional! But it would be great if you have a sample app that you could add to
`trace examples repository <https://github.com/Datadog/trace-examples>`_ along
with screenshots of some example traces in the PR description.

These applications are helpful to quickly spin up example app to test as well
as see how traces look like for that integration you added.
