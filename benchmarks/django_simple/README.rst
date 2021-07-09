Simple Django Performance Scenario
==================================

This application image uses a simple Django application for performance testing ``ddtrace``.

Build
-----

Use docker to build the image which will run tests and store output in an artifacts directory::

  docker build -t django_simple -f benchmarks/django_simple.Dockerfile benchmarks/

Run
---

To run, execute::

  docker run -it --rm django_simple

If you want to save the output, mount a volume to ``/app/output``::

  docker run -it --rm -v "/path/to/output":"/artifacts/output" django_simple

This image will by default install the release version of ``ddtrace``.

You can install a different version by using git tags or commit hashes as the value for the ``DDTRACE_GIT_COMMIT_ID`` environment variable::

  docker run -it --rm -e "DDTRACE_GIT_COMMIT_ID=v0.48.1" django_simple

You can also install from a set of wheel by mounting a volume with the necessary wheels and setting the ``DDTRACE_WHEELS`` environment variable::

  docker run -it --rm  -e "DDTRACE_WHEELS=/artifacts/wheels" -v "/path/to/wheels/":"/artifacts/wheels/" django_simple
