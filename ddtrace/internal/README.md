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
| `apm_tracing_rc: (dict, ddtrace.settings._core.Config) -> None` | Product-specific remote configuration handler (e.g. remote enablement) |
| `before_fork() -> None` | A function with the logic required to prepare the product for a fork |


## Remote Configuration Callbacks

Remote Configuration (RC) allows products to receive configuration updates from
Datadog at runtime. To integrate with RC, products must implement the
`RCCallback` interface.

### The RCCallback Interface

The `RCCallback` abstract base class is defined in
`ddtrace.internal.remoteconfig` and provides two methods:

```python
from ddtrace.internal.remoteconfig import RCCallback, Payload
from typing import Sequence

class MyProductCallback(RCCallback):
    def __call__(self, payloads: Sequence[Payload]) -> None:
        """Process configuration payloads received from Remote Config.

        This method is called whenever new configuration is received for your product.
        It runs in the subscriber process and should process the payloads to update
        product state/configuration.

        Args:
            payloads: Sequence of configuration payloads to process. Each payload
                     contains metadata (product_name, id, etc.) and content (dict).
        """
        for payload in payloads:
            # Process each configuration update
            ...

    def periodic(self) -> None:
        """Perform periodic operations at every polling interval.

        This method is called once per polling cycle (before payload processing),
        regardless of whether new configuration was received. Use this for:
        - Emitting periodic status/metrics
        - Checking for stale state
        - Performing time-based maintenance

        This method is optional - the default implementation does nothing.
        """
        ...
```

### Method Details

#### `__call__(payloads: Sequence[Payload]) -> None`
**Required.** This abstract method must be implemented to process configuration
payloads.

- **When called**: Invoked by the RC subscriber when new configuration is
  received for your product
- **Where it runs**: In the subscriber thread (separate from main thread)
- **Thread safety**: Must be thread-safe as it's called from the subscriber
  thread
- **Payload structure**: Each `Payload` contains:
  - `metadata: ConfigMetadata` - Product name, config ID, hash, version
  - `path: str` - Configuration path
  - `content: dict | None` - Configuration data (None for removals)

#### `periodic() -> None`
**Optional.** Override this method to perform periodic operations.

- **When called**: Once per polling cycle, before any payload processing
- **Frequency**: Determined by RC polling interval (typically every few seconds)
- **Use cases**:
  - Emit periodic status or metrics
  - Check for expired/stale state
  - Perform time-based cleanup
  - Send heartbeats
- **Default behavior**: No-op (does nothing)

### Registering a Callback

To receive RC updates, register your callback with the Remote Config poller:

```python
from ddtrace.internal.remoteconfig import remoteconfig_poller

# Create callback instance
callback = MyProductCallback()

# Register for a product
remoteconfig_poller.register(
    product=PRODUCTS.MY_PRODUCT,  # e.g., "ASM_FEATURES", "LIVE_DEBUGGING"
    callback=callback,
    preprocess=None,  # Optional: preprocessing function (runs in main process)
    skip_enabled=False,  # Set True to skip enabling RC client
    capabilities=[],  # Optional: RC capabilities to report
)
```

**Registration parameters:**
- `product` (str): Product identifier (use constants from `PRODUCTS`)
- `callback` (RCCallback): Your callback instance
- `preprocess` (callable, optional): Function to preprocess payloads in main
  process before subscriber
- `skip_enabled` (bool): If True, don't auto-enable RC client on registration
- `capabilities` (list): RC capabilities to advertise for this product


### Best Practices

1. **Keep `__call__` fast**: The subscriber thread processes all products
   sequentially, so slow callbacks delay other products
2. **Handle errors gracefully**: Wrap processing logic in try/except to avoid
   crashing the subscriber
3. **Use `periodic()` for time-based operations**: Don't do periodic work in
   `__call__` since it only runs on updates
4. **Thread safety**: Both methods run in the subscriber thread - ensure any
   shared state is thread-safe
5. **Clean registration**: Call `remoteconfig_poller.unregister(product)` in
   cleanup/test teardown

### Preprocessing (Advanced)

For advanced use cases, you can provide a preprocessing function that runs in
the main process before payloads are sent to the subscriber:

```python
def preprocess_payloads(payloads: list[Payload]) -> list[Payload]:
    """Preprocess payloads in main process before subscriber receives them."""
    # Perform expensive operations in main process
    # Can modify, filter, or enrich payloads
    return [p for p in payloads if should_process(p)]

remoteconfig_poller.register(
    product="MY_PRODUCT",
    callback=my_callback,
    preprocess=preprocess_payloads,  # Runs in main process
)
```

Preprocessing is useful for:
- Heavy computations that shouldn't block the subscriber
- Filtering payloads before subscriber processing
- Enriching payloads with main process state
- Managing product registration based on configuration (e.g., 1-click
  activation)
