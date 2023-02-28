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


Logging
=======

The ddtrace logger should be used to log events in the dd-trace-py library. Use ``ddtrace.internal.logger.get_logger(__name__)`` to initialize/retrieve an instance of the ddtrace logger (DDLogger).

To ensure the ddtrace library produces consistent and secure logs the following best practices should be followed:

* Logs should be generated with the level DEBUG, INFO, WARNING, or ERROR.
* Log messages are grammatically correct and do not contain spelling errors.
* Log messages should be standalone and actionable. They should not require context from other logs, metrics or trace data.
* Log data is sensitive and should not contain application secrets or other sensitive data.


Release Notes
=============
Release notes are the primary product documentation a user will see when updating the library. Therefore, we must take care to ensure the quality of release notes.

A release note entry should be included for every pull request that changes how a user interacts with the library.

Requiring a Release Note
++++++++++++++++++++++++

A release note is **required** if a PR is user-impacting, or if it meets any of the following conditions:

* `Breaking change to the public API <https://ddtrace.readthedocs.io/en/stable/versioning.html#release-versions>`_
* New feature
* Bug fix
* Deprecations
* Dependency upgrades

Otherwise, a release note is not required.
Examples of when a release note is **not required** are:

* CI chores (e.g., upgrade/pinning dependency versions to fix CI)
* Changes to internal API (Non-public facing, or not-yet released components/features)

Release Note Style Guidelines
+++++++++++++++++++++++++++++

The main goal of a release note is to provide a brief overview of a change.
If necessary, we can also provide actionable steps to the user.

The release note should clearly communicate what the change is, why the change was made,
and how a user can migrate their code.

The release note should also clearly distinguish between announcements and user instructions. Use:

* Past tense for previous/existing behavior (ex: ``resulted, caused, failed``)
* Third person present tense for the change itself (ex: ``adds, fixes, upgrades``)
* Active present infinitive for user instructions (ex: ``set, use, add``)

Release notes should:

* Use plain language.
* Be concise.
* Include actionable steps with the necessary code changes.
* Include relevant links (bug issues, upstream issues or release notes, documentation pages).
* Use full sentences with sentence-casing and punctuation.
* Before using Datadog specific acronyms/terminology, a release note must first introduce them with a definition.

Release notes should not:

* Be vague. Example: ``fixes an issue in tracing``.
* Use overly technical language.
* Use dynamic links (``stable/latest/1.x`` URLs). Instead, use static links (specific version, commit hash) whenever possible so that they don't break in the future.

Generating a Release Note
+++++++++++++++++++++++++
Release notes are generated with the command line tool ``reno`` which can be used with riot::

    $ riot run reno new <title-slug>

The ``<title-slug>`` is used as the prefix for a new file created in ``releasenotes/notes``.
The ``<title-slug>`` is used internally and is not visible in the the product documentation.

Generally, the format of the ``<title-slug>`` is lowercase words separated by hyphens.

For example:

* ``fix-aioredis-catch-canceled-error``
* ``deprecate-tracer-writer``

Release Note Sections
+++++++++++++++++++++

Generated release note files are templates and include all possible categories.
All irrelevant sections should be removed for the final release note.
Once finished, the release note should be committed with the rest of the changes.

* Features: New features such as a new integration or component. For example::

    features:
    - |
      graphene: Adds support for ``graphene>=2``. `See the graphql documentation <https://ddtrace.readthedocs.io/en/1.6.0/integrations.html#graphql>`_
      for more information.

* Upgrade: Enhanced functionality or if dependencies are upgraded. Also used for if components are removed. Usually includes instruction or recommendation to user in regards to how to adjust to the new change. For example::

    upgrade:
    - |
      tracing: Use ``Span.set_tag_str()`` instead of ``Span.set_tag()`` when the tag value is a
      text type as a performance optimization in manual instrumentation.

* Deprecations: Warning of a component being removed from the public API in the future. For example::

    deprecations:
    - |
      tracing: ``ddtrace.Span.meta`` has been deprecated. Use ``ddtrace.Span.get_tag`` and ``ddtrace.Span.set_tag`` instead.

* Fixes: Bug fixes. For example::

    fixes:
    - |
      django: Fixes an issue where a manually set ``django.request`` span resource would get overwritten by the integration.

* Other: Any change which does not fall into any of the above categories. For example::

    other:
    - |
      docs: Adds documentation on how to use Gunicorn with the ``gevent`` worker class.

* Prelude: Not required for every change. Required for major changes such as a new component or new feature which would benefit the user by providing additional context or theme. For example::

    prelude: >
      dynamic instrumentation: Dynamic Instrumentation allows instrumenting a running service dynamically
      to extract runtime information that could be useful for, e.g., debugging
      purposes, or to add extra metrics without having to make code changes and
      re-deploy the service. See https://ddtrace.readthedocs.io/en/1.6.0/configuration.html
      for more details.
    features:
    - |
      dynamic instrumentation: Introduces the public interface for the dynamic instrumentation service. See
      https://ddtrace.readthedocs.io/en/1.6.0/configuration.html for more details.

Release Note Formatting
+++++++++++++++++++++++

In general, a release note entry should follow the following format::

  ---
  <section>:
    - |
      scope: note

Scope
~~~~~

This is a one-word scope, which is ideally the name of the library component, sub-component or integration
that is impacted by this change. This should not be capitalized unless it is an acronym.

To ensure consistency in component naming, the convention in referring to components is as follows:

* Tracer: ``tracing``
* Profiler: ``profiling``
* Application Security Monitoring: ``ASM``
* Dynamic Instrumentation: ``dynamic instrumentation``
* CI Visibility: ``CI visibility``
* Integrations: ``integration_name``

Note
~~~~

The note is a brief description of the change. It should consist of full sentence(s) with sentence-case capitalization.
The note should also follow valid restructured text (RST) formatting. See the template release note for
more details and instructions.

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

All spans generated by the integration must add the tag ``component:<integration_name>`` to each span.
`Example of component tag being set in Flask integration <https://github.com/DataDog/dd-trace-py/blob/a01c18f20de2348ed34bde3ac2fe7a1e010a2d38/ddtrace/contrib/flask/patch.py#L486-L487>`_.

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


Snapshot Tests
++++++++++++++

Many of the tests are based on "snapshots": saved copies of actual traces sent to the
`APM test agent <../README.md#use-the-apm-test-agent>`_.

To update the snapshots expected by a test, first update the library and test code to generate
new traces. Then, delete the snapshot file corresponding to your test. Use `docker-compose up -d testagent`
to start the APM test agent, and re-run the test. Use `--pass-env` as described
`here <../README.md#use-the-apm-test-agent>`_ to ensure that your test run can talk to the
test agent. Once the run finishes, the snapshot file will have been regenerated.


Trace Examples
++++++++++++++

Optional! But it would be great if you have a sample app that you could add to
`trace examples repository <https://github.com/Datadog/trace-examples>`_ along
with screenshots of some example traces in the PR description.

These applications are helpful to quickly spin up example app to test as well
as see how traces look like for that integration you added.
