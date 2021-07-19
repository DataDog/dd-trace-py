Simple Django Performance Scenario
==================================

This application image uses a simple Django application for performance testing ``ddtrace``.

Build
-----

Use docker to build the image which will run tests and store output in an artifacts directory::

  docker build -t django_simple --build-arg BENCHMARK=django_simple -f base.Dockerfile .

Run
---

To run, execute::

  docker run -it --rm -e DDTRACE_GIT_COMMIT_ID_1=master -e DDTRACE_GIT_COMMIT_ID_2=v0.50.0 -e RUN_ID=(uuidgen) django_simple

If you want to save the output, mount a volume to ``/artifacts/``.
