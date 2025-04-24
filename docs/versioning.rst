.. _versioning:

**********
Versioning
**********

Release support
===============
See our public documentation for versioning information. `public documentation <https://docs.datadoghq.com/tracing/trace_collection/compatibility/python/#releases>`_

.. _versioning_interfaces:

Interfaces
==========

For semantic versioning purposes, the public API is defined as follows.

The definition of the **public** interface:
    Any module, function, class or attribute that is not internal


The definition of the **internal** interface:
    The ``ddtrace.vendor`` module is internal

    Any package with the name ``internal`` is internal (e.g. ``ddtrace.internal``, ``ddtrace.contrib.internal``)

    Any module, function, class or attribute that is prefixed with a single underscore is internal

    Any module, function, class or attribute that is contained within an internal module is internal

Internal code may be subject to breaking changes in bug fix and minor releases.
