**********
Versioning
**********


Current Status
==============

.. list-table:: Releases
   :widths: 20 30 30 20
   :header-rows: 1

   * - Release
     - Supported Runtimes
     - Minimum Trace Agent
     - Status
   * - 0.x
     - CPython: 2.7, 3.5+
     - 
     - MAINTENANCE [2022-02-25]
   * - 1.x
     - CPython: 2.7, 3.5+
     - 7.28
     - CURRENT


The **status** of each release determines when changes are made:

MAINTENANCE
    No longer under development.

    Receives bug fixes and security fixes.

CURRENT
    Under active development.

    Receives bug fixes and security fixes.

    Receives backwards compatible features.

DEVELOPMENT
    Under active development.

    Receives backwards incompatible features.

    Will become the next CURRENT release.


Interface
=========

The definition of the **internal** interface:
    The ``ddtrace.internal`` package is internal

    The ``ddtrace.vendor`` package is internal

    Any module, function, class or attribute that is prefixed with a single underscore is internal

    Any package, module, function, class or attribute that is contained within an internal namespace is also internal


The definition of the **public** interface:
    Any package, module, function, class or attribute that is not internal



Versions
========

The non-negative integer components of the **version format** (``v<MAJOR>.<MINOR>.<PATCH>``) are incremented by the criteria:

MAJOR
    API changes incompatible with previous versions

    Functionality changes incompatible with previous versions

    Dropping support for Python versions.

MINOR
    API changes that are backwards compatible

    Functionality changes that are backwards compatible

    Adding support for anything, but not limited to, Python versions, library versions supported in integrations, or Datadog APM features.

PATCH
    Bug fixes that are backwards compatible

    Security fixes that are backwards compatible
