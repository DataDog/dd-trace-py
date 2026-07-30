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
| `post_preload() -> None` | A function with the logic required to finish initialization after the library  preload stage |
| `enabled() -> bool` | A function that returns whether the product should be started; called before `start()` by the product manager |
| `start() -> None` | A function with the logic required to enable the product (called only when `enabled()` returns `True`) |
| `restart(join: bool = False) -> None` | A function with the logic required to restart the product after a fork |
| `stop(join: bool = False) -> None` | A function with the logic required to stop the product; also called at process exit (see `skip_exit`) |

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
| `skip_exit() -> bool` | Return `True` to skip calling `stop()` at process exit; use when the product registers its own `atexit` hooks or when a graceful shutdown is unnecessary |
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

### The RemoteConfigPoller API

The `RemoteConfigPoller` (exposed as the `remoteconfig_poller` singleton)
exposes two orthogonal operations that must be managed independently:

1. **Callback registration** — installs the handler that receives RC payloads.
   A registered callback also receives periodic calls every poll cycle, even
   when no new configuration arrives.

2. **Product enablement** — controls whether the product name is included in
   the `products` field of the client payload sent to the agent.  Only enabled
   products are advertised to the agent; only advertised products get
   configuration pushed back to them.

The two operations are deliberately separate because a product may need its
callback to be active (e.g. for periodic housekeeping) without yet requesting
configuration from the agent, or vice-versa.

#### `register_callback(product, callback, capabilities=[])`

Installs a callback for a product.  The callback will receive all payloads
dispatched by the RC subscriber, as well as periodic calls.  If this is the
first callback being registered, the RC poller is started automatically (if
`DD_REMOTE_CONFIGURATION_ENABLED` is set).

Registering a callback **does not** enable the product: the product name will
**not** appear in client payloads until `enable_product()` is called.

```python
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

callback = MyProductCallback()
remoteconfig_poller.register_callback(
    "MY_PRODUCT",
    callback,
    capabilities=[MyCapabilities.SOME_FLAG],
)
```

#### `unregister_callback(product)`

Removes the callback for a product.  The callback will no longer receive
payloads or periodic calls.  This **does not** disable the product: if
`enable_product()` was called previously, the product name will still appear in
client payloads until `disable_product()` is called.

```python
remoteconfig_poller.unregister_callback("MY_PRODUCT")
```

#### `enable_product(product)`

Adds the product name to the `products` field of client payloads sent to the
agent, signalling that this client wants to receive configurations for it.
`register_callback()` must be called first so that the agent's responses can be
dispatched to a handler.

```python
remoteconfig_poller.enable_product("MY_PRODUCT")
```

#### `disable_product(product)`

Removes the product name from client payloads.  The callback, if still
registered, remains active and will continue to receive any payloads the agent
may still send for that product.

```python
remoteconfig_poller.disable_product("MY_PRODUCT")
```

#### `update_product_callback(product, callback)`

Replaces the callback for an already-registered product without affecting its
enabled/disabled state or capabilities.  Returns `True` if the product was
found, `False` otherwise.  This is primarily used after a fork, when a new
callback instance must be installed for the child process.

```python
new_callback = MyProductCallback()
remoteconfig_poller.update_product_callback("MY_PRODUCT", new_callback)
```

### Typical Lifecycle

Most products follow this pattern in their `start()` / `stop()` functions:

```python
def start():
    # 1. Register the callback so the subscriber can dispatch payloads.
    remoteconfig_poller.register_callback("MY_PRODUCT", MyProductCallback())
    # 2. Advertise the product to the agent so it starts sending configuration.
    remoteconfig_poller.enable_product("MY_PRODUCT")

def stop(join=False):
    # 1. Stop requesting configuration from the agent.
    remoteconfig_poller.disable_product("MY_PRODUCT")
    # 2. Remove the callback.
    remoteconfig_poller.unregister_callback("MY_PRODUCT")
```

A product that only needs its callback active for internal housekeeping but
does not yet want the agent to push configuration can call
`register_callback()` without `enable_product()`.

### Best Practices

1. **Keep `__call__` fast**: The subscriber thread processes all registered
   products sequentially, so a slow callback delays all others.
2. **Handle errors gracefully**: Wrap processing logic in try/except to avoid
   crashing the subscriber.
3. **Use `periodic()` for time-based operations**: `__call__` is only invoked
   when new configuration arrives; use `periodic()` for work that must happen
   every poll cycle.
4. **Thread safety**: Both `__call__` and `periodic()` run in the subscriber
   thread — protect any shared state that is also accessed from the main thread.
5. **Always pair register with unregister**: Call `unregister_callback()` (and
   `disable_product()` if applicable) in your product's `stop()` to release
   resources and stop advertising the product to the agent.


## Fork Safety

`ddtrace` runs in applications that use `os.fork()` — web servers (Gunicorn,
uWSGI), multiprocessing workers, test runners (pytest-xdist), and third-party
SDKs (e.g. Stripe's Python client under gevent). Getting fork wrong causes
duplicate traces, deadlocks, or silent data loss, so the internal machinery has
a deliberate fork-safety protocol.

### The `forksafe` module

`ddtrace.internal.forksafe` is the single registration point for all fork
hooks. It wraps `os.register_at_fork()` and exposes three ordered registries:

| Registry | When it runs | Typical use |
|----------|-------------|-------------|
| `_registry_before_fork` | In the parent, before `fork()` | Flush state, acquire no new locks, stop threads |
| `_registry` (`after_in_child`) | In the child, after `fork()` | Reinitialize resources that cannot be shared |
| `_registry_after_parent` | In the parent, after `fork()` | Resume any work paused in `before_fork` |

Use the helpers to register hooks:

```python
from ddtrace.internal import forksafe

forksafe.register_before_fork(my_before_hook)
forksafe.register(my_after_child_hook)        # after_in_child
forksafe.register_after_parent(my_after_parent_hook)
```

**Keep hooks non-blocking.** By the time a hook registered *after*
`ddtrace.internal.threads._before_fork` runs, all periodic threads have already
been stopped, so there are no locks held by those threads to worry about. (Hooks
registered *before* that point run while periodic threads may still be active,
so they must not assume threads have stopped.) The concern is speed: a hook that
blocks
delays the moment the child process becomes ready. Many parent processes (e.g.
pre-fork web servers) wait with a bounded timeout for a signal from the child
confirming it has started successfully. A slow hook can exhaust that timeout,
causing the parent to treat the child as failed even though nothing went wrong.

### `PeriodicThread` fork protocol (`_threads.cpp`)

`PeriodicThread` (the C extension backing `ddtrace.internal.periodic`)
**guarantees fork safety** through a two-phase protocol: all active periodic
threads are stopped before the fork, then automatically restarted in both the
parent and the child afterwards.

**Phase 1 — before fork**

Every active `PeriodicThread` is stopped cleanly. Critically, the `on_shutdown`
callback is suppressed during this stop so that writers do not flush their
queues into the child — flushing would duplicate traces already buffered in
the parent.

> **Important:** `_before_fork` calls `join()` on every active worker with no
> timeout. Blocking operations in a `periodic()` callback are fine, but they
> must use their own timeouts — an unbounded block will prevent `fork()` from
> returning until the operation completes, which can hang pre-fork servers.

**Phase 2 — after fork**

Both the parent and the child call `_after_fork()` on every thread. In the
child, restart is controlled by the `__autorestart__` attribute (default
`True`): threads that leave it `True` are restarted transparently; threads that
set it to `False` remain stopped and must arrange their own re-initialization
(see `NativeWriter` below).

In the parent, restarts are **asynchronous**: `_after_fork_parent` merely calls
`ThreadRestartTimer.set()`, which schedules a deferred restart. The actual
`_after_fork(force=True)` calls happen later, coalesced across repeated rapid
forks. Parent-side periodic workers are therefore not immediately active after
`fork()` returns; components that rely on parent-side metrics or flushing
immediately after a fork should account for this delay.

The default `__autorestart__ = True` means that for most `PeriodicService`
subclasses **no fork-specific code is required** — the protocol handles
everything automatically.

**Periodic threads must not synchronize with each other.** Because all
periodic threads are stopped before the fork while holding their own internal
locks, any attempt to acquire a second thread's lock from within a fork hook
(or from within the thread's own shutdown path) risks a lock-ordering deadlock.
Periodic threads are therefore not allowed to synchronize among themselves;
each thread's lock must be acquired only by that thread and by the fork
machinery in a well-defined order.

### Adding new fork hooks

- Register via `forksafe` — never call `os.register_at_fork()` directly.
- Keep hooks **non-blocking** and **side-effect free** where possible.
- If your component owns a `PeriodicService` **and has an explicit child
  reinitialization path** (e.g. a `_child_after_fork` hook that recreates the
  service), set `__autorestart__ = False` to suppress the automatic restart of
  the inherited worker. Do not set this without a matching reinitialization
  path — the service will be left marked as running with no active worker.
- Never call `flush_queue()` or perform I/O in an after-child hook; flush only
  in after-parent hooks or at clean shutdown.
