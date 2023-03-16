"""
Datadog Source Code Integration
===============================

`Datadog Source Code
Integration <https://docs.datadoghq.com/integrations/guide/source-code-integration/>`__ is supported for Git by the addition of the repository URL and commit hash in the Python package metadata field ``Project-URL`` with name
``source_code_link``.

Format::

  repository_url#commit_hash


setuptools
----------

The ``ddtrace`` provides automatic instrumentation of ``setuptools`` to embed the source code link into the project metadata. The package has to be installed as a build dependency.

Packages with ``pyproject.toml`` can update the build system requirements::

  [build-system]
  requires = ["setuptools", "ddtrace"]
  build-backend = "setuptools.build_meta"


The instrumentation of ``setuptool`` can be automatically enabled to embed the source code link is provided with a one-line import::

   import ddtrace.sourcecode.setuptools_auto
   from setuptools import setup

   setup(
       name="mypackage",
       version="0.0.1",
       #...
   )
"""
