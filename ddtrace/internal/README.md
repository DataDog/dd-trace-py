# Internal
This internal module is used to define and document an internal only API for `ddtrace`.

These modules are not intended to be used outside of `ddtrace`.

The APIs found within `ddtrace.internal` are subject to breaking changes at any time
and do not follow the semver versioning scheme of the `ddtrace` package.


## The Product Protocol

New products can be introduced in a modular way by defining a new product
object that implements the product protocol. This consists of a Python object
(generally a module) that exports the following mandatory attributes:

| Attribute | Description |
|-----------|-------------|
| `start() -> None` | A function with the logic required to start the product |
| `restart(join: bool = False) -> None` | A function with the logic required to restart the product after a fork |
| `stop(join: bool = False) -> None` | A function with the logic required to stop the product |
| `at_exit(join: bool = False) -> None` | A function with the logic required to stop the product at exit |
| `post_preload() -> None` | A function with the logic required to finish initialization after the library  preload stage |

The product object needs to be made available to the Python plugin system by
defining an entry point in the `project.entry-points.'ddtrace.products'` section
of the `pyproject.toml` file.

> [!NOTE]
> The `post_preload` method is part of the gevent support via module cloning.
> Once a different solution will be in place, this function will likely be
> removed. Any initialization logic that needs to be executed on load can be
> moved to the `start` method, or as part of the product object initialization.

Products can also define optional attributes that are used to manage other
aspects of a product's lifecycle. This list can grow over time as the protocol
gets extended to add support for additional features.

| Attribute | Description |
|-----------|-------------|
| `requires: list[str]` | A list of other product names that the product depends on |
| `config: DDConfig` | A configuration object; when an instance of `DDConfig`, configuration telemetry is automatically reported |
| `APMCapabilities: Type[enum.IntFlag]` | A set of capabilities that the product provides |
| `apm_tracing_rc: (dict) -> None` | Product-specific remote configuration handler (e.g. remote enablement) |
| `before_fork() -> None` | A function with the logic required to prepare the product for a fork |
