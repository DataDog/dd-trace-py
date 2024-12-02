.. _versioning:

**********
Versioning
**********

Release support
===============

.. list-table::
   :header-rows: 1

   * - Release
     - :ref:`Support level<versioning_support_levels>`
     - Minimum Datadog Agent
   * - ``<1``
     - :ref:`End of Life<versioning_support_eol>`
     -
   * - ``>=1.0,<2``
     - :ref:`Maintenance<versioning_support_maintenance>`
     - 7.28
   * - ``>=2.0,<3``
     - :ref:`General Availability<versioning_support_ga>`
     - 7.28

.. _versioning_support_levels:

Support levels
==============

.. list-table::
   :header-rows: 1

   * - Level
     - Policy

       .. _versioning_support_ga:
   * - General Availability (GA)
     - All fixes are backported to the three most recent General Availability minor release lines.

       .. _versioning_support_maintenance:
   * - Maintenance
     - The most recent Maintenance minor release line receives security fixes and selected bug fixes.

       .. _versioning_support_eol:
   * - End-of-Life (EOL)
     - Receives no updates or support

.. _versioning_release:

Release versions
================

The non-negative integer components of the **version format** (``v<MAJOR>.<MINOR>.<PATCH>``) are incremented by the criteria:

MAJOR
    Incompatible changes to the public :ref:`interface<versioning_interfaces>`

    Removing support for a :ref:`runtime<versioning_supported_runtimes>`.

MINOR
    Backwards compatible changes to the public :ref:`interface<versioning_interfaces>`

    Any backwards compatible or incompatible changes to the internal :ref:`interface<versioning_interfaces>`

    Adding support for a :ref:`runtime<versioning_supported_runtimes>`

PATCH
    Bug fixes that are backwards compatible

    Security fixes that are backwards compatible

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

.. _versioning_supported_runtimes:

Supported runtimes
==================


.. list-table::
   :header-rows: 1

   * - OS
     - CPU
     - Runtime
     - Runtime version
     - Supported Release
   * - Linux
     - x86-64, i686, AArch64
     - CPython
     - 3.7-3.12
     - ``>=2.0,<3``
   * - MacOS
     - Intel, Apple Silicon
     - CPython
     - 3.7-3.12
     - ``>=2.0,<3``
   * - Windows
     - 64bit, 32bit
     - CPython
     - 3.7-3.12
     - ``>=2.0,<3``
   * - Linux
     - x86-64, i686, AArch64
     - CPython
     - 2.7, 3.5-3.11
     - ``>=1.0,<2``
   * - MacOS
     - Intel, Apple Silicon
     - CPython
     - 2.7, 3.5-3.11
     - ``>=1.0,<2``
   * - Windows
     - 64bit, 32bit
     - CPython
     - 2.7, 3.5-3.11
     - ``>=1.0,<2``
