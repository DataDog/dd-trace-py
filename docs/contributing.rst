==============
 Contributing
==============

When contributing to this repository, we advise you to discuss the change you
wish to make via an `issue <https://github.com/DataDog/dd-trace-py/issues>`_.

Branches
========

Developement happens in the `master` branch. When all the features for the next
milestone are merged, the next version is released and tagged on the `master`
branch as `vVERSION`.

Your pull request should targets the `master` branch.

Once a new version is released, a `release/VERSION` branch might be created to
support micro releases to `VERSION`. Patches should be cherry-picking from the
`master` branch where possible — or otherwise created from scratch.


Pull Request Process
====================

In order to be merged, a pull request needs to meet the following
conditions:

1. The test suite must pass.
2. One of the repository Members must approve the pull request.
3. Proper unit and integration testing must be implemented.
4. Proper documentation must be written.

Splitting Pull Requests
=======================

If you discussed your feature within an issue (as advised), there's a great
chance that the implementation appears doable in several steps. In order to
facilite the review process, we strongly advise to split your feature
implementation in small pull requests (if that is possible) so they contain a
very small number of commits (a single commit per pull request being optimal).

That ensures that:

1. Each commit passes the test suite.
2. The code reviewing process done by humans is easier as there is less code to
   understand at a glance.

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

Libraries Support
=================

External libraries support is implemented in submodules of the `ddtest.contrib`
module.

Our goal is to support:

- The latest version of a library.
- All versions of a library that have been released less than 1 year ago.

Support for older versions of a library will be kept as long as possible as
long as it can be done without too much pain and backward compatibility — on a
best effort basis. Therefore, support for old versions of a library might be
dropped from the testing pipeline at anytime.

Code Style
==========

The code style is enforced by `flake8 <https://pypi.org/project/flake8>`_, its
configuration, and possibly extensions. No code style review should be done by
a human. All code style enforcement must be automatized to avoid bikeshedding
and losing time.


How-to: Write an Integration
============================

First off, thank you for writing a new integration! The best way to get started
writing a new integration is to refer to existing integrations, preferably one
that instruments a similarly themed library or framework. For example, to write
a new integration for ``memcached`` we might refer to the existing ``redis``
integration as a starting point.

The key focus of an integration is to provide concise, insightful information
about the library or framework that'll be useful for developers to monitor their application as a
whole. The goal isn't to monitor the library itself per se, but to monitor how
the application is using and interacting with the library. So we care less about
the nitty-gritty underlying implementation details of the library and more so
about the public API in which developers interact with and know about.

There are a number of things to keep in mind while writing an integration:


Preserving Behaviour
++++++++++++++++++++

Arguably the most important aspect of the integration should be that it interferes
in no way (or as little as possible) with the library or application. Attention
should be paid to CPU and memory usage of the integration. It is also unacceptable
for the integration to raise uncaught exceptions.


Configuration
+++++++++++++
TODO?

Library Support
+++++++++++++++

``ddtrace`` tries to support as many active versions of a library as possible.
Because of this, it can become tricky to instrument a library due to changing
features and APIs.

For tricky libraries it's recommended to pull out the version of the library to
use when instrumenting volatile features. A great example of this is the Flask
integration:

    - pulling out the version: `flask version <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L45-L58>`_
    - using it to instrument a later-added feature `flask version usage <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L149-L151>`_


Database Integrations
+++++++++++++++++++++

``ddtrace`` already provides base instrumentation for the Python database API
(PEP 249) which most database client libraries implement in the
`ddtrace.contrib.dbapi <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/dbapi/__init__.py>`_
module.

Check out some of our existing database integrations for how to use the `dbapi`:

    - `psycopg <https://github.com/DataDog/dd-trace-py/tree/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/psycopg>`_
    - `mysql <https://github.com/DataDog/dd-trace-py/tree/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/mysql>`_


Exceptions/Errors
+++++++++++++++++

Exceptions provide a lot of useful information about errors and the application
as a whole and are fortunately usually quite easy to deal with. Exceptions are
a great place to start instrumenting. There are a couple of considerations when
dealing with exceptions in ``ddtrace``:

    - Re-raising the exception: it is crucial that we do not interfere with the
      application, so exceptions must be re-raised. See the `bottle exception handling <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/bottle/trace.py#L50-L69>`_
      instrumentation for an example.

    - Gathering relevant information: exceptions usually contain a lot of
      relevant information for tracking down a bug. ``ddtrace`` provides
      a helper for pulling out this information and adding it to a span.
      See the `cassandra exception handling <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/cassandra/session.py#L117-L122>`_
      instrumentation for an example.


Enable/Disable Logic
++++++++++++++++++++
TODO?

Distributed Tracing
+++++++++++++++++++

Some integrations pass information across application boundaries to other
applications where the request is continued. Datadog and ``ddtrace`` provide
support for continuing a trace in another application. Distributed tracing only makes
sense for libraries that send or receive requests across application boundaries.

    - Propagating the trace example: `requests <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/requests/connection.py#L85-L88>`_
    - Receiving a propagated trace example: `Django <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/django/middleware.py#L116-L121>`_


Testing
+++++++

Testing is the most important part of the integration. We have to be certain
that the integration:

    1) works: submits meaningful information to Datadog

    2) is invisible: does not impact the library or application by disturbing state,
       performance or raising exceptions


Testing integrations is hard. There are often many versions of the library to go
along with the different versions of Python.


Testing checklist (with the ``redis`` integration as an example):

    - [ ] `tox.ini configuration <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/tox.ini#L97>`_
    - [ ] `docker-compose.yml configuration (if applicable) <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/docker-compose.yml#L37-L40>`_
    - [ ] `.circleci/config.yml <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/.circleci/config.yml#L614-L624>`_
    - [ ] Integration is configurable and all the configuration options are
      hooked up and functional
    - [ ] Spans contain meaningful/correct data
    - [ ] No uncaught exceptions are raised from the integration
    - [ ] Distributed tracing (if applicable)
