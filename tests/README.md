# Dynamic Job Runs

This repository makes use of dynamic features of CI providers to run only the
jobs that are necessary for the changes in the pull request. This is done by
giving a logical description of the test suites in terms of _components_. A
component is a logical grouping of tests that can be run independently of other
components. For example, a component could be a test suite for a specific
package, or a test suite for a specific feature, e.g. the tracer, the profiler,
etc... . Components may depend on each other, provide that the resulting
dependency graph is acyclic. For example, the library has a _core_ component
on which most, if not all, other components depend. When a change is made to the
core component, all other components depending on it must be re-tested. This
logical description is contained in the `/tests/.suitespec.json` file.

The jobs to be run are defined in `jobspec.yml` files under the `/tests`
sub-tree. Each entry has the name of a suite from the suitespec file, and
defines the parameters for the runner image. The general structure of a job spec
is

```yaml
suite_name: # A suite name from the .suitespec.json file
  runner: # The runner image to use (riot | hatch)
  is_snapshot: # Whether this is a snapshot test run (optional, defaults to false)
  services: # Any services to spawn for the tests (e.g. redis, optional)
  env: # Environment variables to set for the tests
    SUITE_NAME: # The suite pattern from the riotfile|hatch env names (mandatory)
  parallelism: # The parallel degree of the job (optional, defaults to 1)
  retry: # The number of retries for the job (optional)
  timeout: # The timeout for the job (optional)
```

To create a new job for a new test suite, create a new suite entry in the
`.suitespec.json` file and declare the file patterns that should trigger it.
Then create a new job in a `jobspec.yml` file under the `/tests` sub-tree with
the format described above, matching the name of the suite in the
`.suitespec.json` just created. The `SUITE_NAME` environment variable must be
a regular expression matching at least one riotfile environment (for the riot
runner) or a hatch environment (for the hatch runner).
