Simple Django Performance Scenario
==================================

This application image uses a simple Django application for performance testing ``ddtrace``.

Build
-----

Use docker to build the image which will run tests and store output in an artifacts directory::

  docker build -t django_simple benchmarks/docker/django_simple/

Run
---

To run, simply execute::

  docker run -it --rm django_simple

If you want to save the output, mount a volume to ``/app/output``::

  docker run -it --rm -v "/path/to/output":"/artifacts/output" django_simple

This image will by default install the release version of ``ddtrace`` or the value of the ``DDTRACE_VERSION`` environment variable if given. You can also install from a set of wheel by mounting a volume with the necessary wheels::

  docker run -it --rm -v "/path/to/wheels/":"/artifacts/wheels/" django_simple
