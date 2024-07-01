# Kubernetes library injection

This directory contains scripts and a Docker image for providing the ddtrace
package via a Kubernetes [init
container](https://kubernetes.io/docs/concepts/workloads/pods/init-containers/)
which allows users to easily instrument Python applications without requiring
changes to the application image.

This Library Injection functionality can be used independently of `ddtrace-run`, `ddtrace.auto`,
and any other "manual" instrumentation mechanism.

## Technical Details

The `Dockerfile` defines the image that is published for `ddtrace` which is used
as a Kubernetes InitContainer. Kubernetes runs it before deployment pods start.
It is responsible for providing the files necessary to run `ddtrace` in an
arbitrary downstream application container. It also provides a script to copy
the necessary files to a given directory.

The `dl_wheels.py` script provides a portable set of `ddtrace` wheels. It is
responsible for downloading the published wheels of `ddtrace` and its
dependencies. It installs the wheels to a set of per Python, per platform site-packages
directories. These directories can be directly added to the `PYTHONPATH` to use the
`ddtrace` package.

The Datadog Admission Controller injects the InitContainer with a new volume
mount to the application deployment. The script to copy files out of the
InitContainer is run to copy the files to the volume. The `PYTHONPATH`
environment variable is injected into the application container along with the
volume mount.

The files copied to the volume are:

- `sitecustomize.py`: Python module that gets run automatically on interpreter startup when it is detected in the `PYTHONPATH`. When executed, it updates the Python path further to include the compatible `site-packages` directory and then calls `import ddtrace.bootstrap.sitecustomize` which performs automatic instrumentation.
- `ddtrace_pkgs/`: Directory containing the per-Python, per-runtime `site-packages` directories which contain `ddtrace` package and its dependencies.


The `PYTHONPATH` environment variable is set to the shared volume directory
which contains `sitecustomize.py` and `ddtrace_pkgs`. The environment variable
is injected into the application container. This enables the
`sitecustomize.py` file to execute on any Python interpreter startup which
results in the automatic instrumentation being applied to the application.


## Testing

To test this feature locally use the provided `docker-compose.yml`.

```bash
export DDTRACE_PYTHON_VERSION=v1.16.1
export APP_CONTEXT=$REPO_ROOT/tests/lib-injection/dd-lib-python-init-test-django
export TEMP_DIR="/tmp/ddtrace"
rm -rf $TEMP_DIR && docker-compose up --build lib_inject && docker-compose up --build
```

Note that the `lib_inject` step is separate to ensure the files are copied to the volume before the app starts up.
