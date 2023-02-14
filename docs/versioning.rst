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
     - :ref:`Maintenance<versioning_support_maintenace>`
     -
   * - ``>=1.0,<2``
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
     - Support for new features, bug and security fixes. Bug and security fixes are backported to the latest minor release branch.

       .. _versioning_support_maintenace:
   * - Maintenance
     - Does not receive new features. Support for critical bug fixes and security fixes only. Applicable bug and security fixes are backported to the latest minor release branch.
   * - End-of-life
     - No support.


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


The definition of the **internal** interface:
    The ``ddtrace.internal`` module is internal

    The ``ddtrace.vendor`` module is internal

    Any module, function, class or attribute that is prefixed with a single underscore is internal

    Any module, function, class or attribute that is contained within an internal module is internal


The definition of the **public** interface:
    Any module, function, class or attribute that is not internal


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
     - 2.7, 3.5-3.11
     - ``<2``
   * - MacOS
     - Intel, Apple Silicon
     - CPython
     - 2.7, 3.5-3.11
     - ``<2``
   * - Windows
     - 64bit, 32bit
     - CPython
     - 2.7, 3.5-3.11
     - ``<2``
