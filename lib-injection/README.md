# Kubernetes library injection

This directory contains scripts and a Docker image for providing the ddtrace
package via a Kubernetes [init
container](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/)
which allows users to easily instrument Python applications without requiring
changes to the application image.

The `Dockerfile` defines the image that is published for `ddtrace` which is used
as a Kubernetes InitContainer. Kubernetes runs it before deployment pods start.
It is responsible for providing the files necessary to run `ddtrace` in an
arbitrary downstream application container. It also provides a script to copy
the necessary files to a given directory.

The `dl_wheels.py` script provides a portable set of `ddtrace` wheels. It is
responsible for downloading the published wheels of `ddtrace` and its
dependencies.

The Datadog Admission Controller injects the InitContainer with a new volume
mount to the application deployment. The script to copy files out of the
InitContainer is run to copy the files to the volume. The `PYTHONPATH`
environment variable is injected into the application container along with the
volume mount.

The files copied to the volume are:

- `sitecustomize.py`: Python module that gets run automatically on interpreter startup when it is detected in the `PYTHONPATH`. When executed, it updates the Python path further to include the `ddtrace_pkgs/` directory and then calls `import ddtrace.bootstrap.sitecustomize` which performs automatic instrumentation.
- `ddtrace_pkgs/`: Directory containing the `ddtrace` package and its dependencies for each Python version, platform and architecture.


The `PYTHONPATH` environment variable is set to the shared volume directory
which contains `sitecustomize.py` and `ddtrace_pkgs`. The environment variable
is injected into the the application container. This enables the
`sitecustomize.py` file to execute on any Python interpreter startup which
results in the automatic instrument being applied to the application.
