Performance Testing
===================

New scenario
------------

Create a new directory for a scenario which includes a ``scenario.py`` script to be run. Add any additional dependencies in a ``requirements_scenario.txt``.

First you need to build the scenario image::

  docker build -t perf-<scenario> --build-arg SCENARIO=<scenario> -f base/Dockerfile .

You can now run the scenario image with two versions of the library. The environment variables ``DDTRACE_INSTALL_{V1,V2}`` can be set to a PEP 508 specification or a git url:

  docker run -it --rm -e DDTRACE_INSTALL_V1="ddtrace" -e DDTRACE_INSTALL_V2="ddtrace==0.50.0" perf-<scenario>
  docker run -it --rm -e DDTRACE_INSTALL_V1=git+https://github.com/Datadog/dd-trace-py@master -e DDTRACE_INSTALL_V2=git+https://github.com/Datadog/dd-trace-py@v0.50.0 perf-<scenario>
