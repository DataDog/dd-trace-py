**********
Versioning
**********


Release support
===============


.. list-table::
   :header-rows: 1

   * - Release
     - :ref:`Support level<versioning_support_levels>`
     - Minimum Trace Agent
   * - 0.x
     - :ref:`Maintenance<versioning_support_maintenace>`
     -
   * - 1.x
     - :ref:`General Availability<versioning_support_ga>`
     - 7.28


Versions
========


The non-negative integer components of the **version format** (``v<MAJOR>.<MINOR>.<PATCH>``) are incremented by the criteria:

MAJOR
    Incompatible changes to the public :ref:`interface<versioning_interfaces>`

    Removing support for a :ref:`runtime<versioning_supported_runtimes>`.

MINOR
    Backwards compatible changes to the public :ref:`interface<versioning_interfaces>`

    Any backwards compatible or incompatible changes to the internal :ref:`interface<versioning_interfaces>`

    Adding a support for :ref:`runtime<versioning_supported_runtimes>`

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
    Any package, module, function, class or attribute that is not internal


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
     - 3.10, 3.9, 3.8, 3.7, 3.6, 3.5, 2.7
     - 1.x
   * - MacOS
     - Intel, Apple Silicon
     - CPython
     - 3.10, 3.9, 3.8, 3.7, 3.6, 3.5, 2.7
     - 1.x
   * - Windows
     - 64bit, 32bit
     - CPython
     - 3.10, 3.9, 3.8, 3.7, 3.6, 3.5, 2.7
     - 1.x


.. _versioning_support_levels:

Support levels
==============


.. list-table::
   :header-rows: 1

   * - Level
     - Policy

       .. _versioning_support_ga:
   * - General Availability (GA)
     - Full implementation of all features. Full support for new features, bug & security fixes.

       .. _versioning_support_maintenace:
   * - Maintenance
     - Full implementation of existing features. Does not receive new features. Support for bug & security fixes only.
   * - End-of-life
     - No support.
