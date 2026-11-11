Architectural Constraints
=========================

This page lists the rules that new code in dd-trace-py follows for fork safety, background
threads, and dependency direction between packages. Each rule has an identifier (``FS-*`` for
fork safety, ``LA-*`` for layering) so reviews can refer to it.

Rules marked **target** describe the agreed design for new code. Some existing code predates
them: unchanged code is not a violation, but a change must not extend a known exception.

The ``architecture-review`` skill in ``.agents/skills/architecture-review/`` checks a change
against these rules.


Fork Safety and Background Threads
----------------------------------

ddtrace runs inside pre-fork servers (Gunicorn, uWSGI), multiprocessing workers and gevent
applications. A fork copies only the forking thread, so any lock, thread or buffer that ddtrace
owns must be in a known state at fork time. The "Fork Safety" section of
``ddtrace/internal/README.md`` explains the machinery; section 2 of
``docs/native-code-review.md`` covers native primitives.

.. _FS-1:

FS-1 Register fork hooks through ``forksafe``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``forksafe.register_before_fork``, ``forksafe.register`` (after in child) and
``forksafe.register_after_parent``. ``forksafe`` is the only caller of ``os.register_at_fork``.
It serializes the ddtrace hooks under one lock from before-fork to after-fork, runs them in a
known order relative to thread stop and restart, isolates exceptions, and also runs the child
hooks for uWSGI workers forked at the C level, where ``os.register_at_fork`` hooks never run.

.. _FS-2:

FS-2 Hooks are fast, and each registry has one job
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- Before fork (parent): pause work. Do not start threads or acquire locks that other threads may
  hold for a long time.
- After in child: recreate process-local state (locks, buffers, caches, sockets, PIDs). No I/O,
  no flushing: data buffered in the parent belongs to the parent, and flushing it from the child
  duplicates it.
- After in parent: resume work paused before the fork.

Every hook delays the moment the forked child becomes ready, and pre-fork servers kill children
that miss a startup deadline. Process managers such as Celery close inherited file descriptors
after the at-fork callbacks return, so native workers must not be rebuilt while
``forksafe.in_child_hook()`` is true. The delayed restart of ``PeriodicThread`` usually avoids
this, but the delay is a fixed time, not a wait for the hooks to finish (`FS-12`_), so code that
rebuilds native workers checks ``forksafe.in_child_hook()`` itself.

.. _FS-3:

FS-3 Background work uses ddtrace thread primitives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
    :header-rows: 1

    * - Need
      - Use
    * - Periodic work
      - ``periodic.PeriodicService`` / ``AwakeablePeriodicService``
    * - Periodic work with state reset on fork
      - ``periodic.ForksafeAwakeablePeriodicService``
    * - One-shot background task
      - ``threads.Thread``
    * - Delayed one-shot callback
      - ``periodic.Timer``

Only these take part in the fork protocol: they are stopped and joined before the fork, and
periodic workers are restarted afterwards. A one-shot ``threads.Thread`` is joined to completion
before the fork and never runs again in either process, so a task that must also run in the
child has to be started again from an after-in-child hook or the product's restart path.

A raw ``threading.Thread`` keeps running in the parent during the fork, may hold locks the child
inherits, and does not exist in the child. Under gevent, a thread created through the
application's patched ``threading`` is a greenlet.

.. _FS-4:

FS-4 Application primitives for the application, ddtrace's own for ddtrace
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The rule of thumb: code that runs on application threads on the application's behalf uses
whatever the application uses, including the result of gevent's monkey-patching; internal
ddtrace usage grabs ddtrace's own primitives.

- Application side: ``threads.current_thread()``, ``threads.Event()`` and ``threads.local()``
  follow the application's ``threading`` module, patched or not. Use them for state and waits
  that belong to the application's execution, for example per-request state or the current
  thread recorded in a snapshot. Under gevent they are per greenlet and yield to the hub.
- ddtrace side: ``threads.Lock``, ``threads.RLock``, ``threads.SimpleQueue``,
  ``threads.original_Event`` and ``threads.original_local`` are real OS-thread primitives taken
  before any patching. So are ``forksafe.Lock()``, ``forksafe.RLock()`` and ``forksafe.Event()``,
  which are also reset after a fork. Use them for ddtrace's own state. Anything a ddtrace thread
  also touches counts as internal, even when application threads use it too (for example the
  lock around a writer's buffer): a green primitive shared with an OS thread can deadlock,
  because the greenlet that would release it cannot run.
- The ``ast-grep`` rules ``threading-lock-usage`` and ``threading-module-import`` enforce the
  imports. Whether a call site picked the right side needs review.
- A lock that must be released in the child is recreated in an after-in-child hook, or created
  with ``forksafe.Lock()`` / ``forksafe.RLock()`` / ``forksafe.Event()`` (a ``ResetObject`` that
  is replaced after the fork). Never release or clear an inherited lock in the child: the owning
  thread no longer exists.
- Native code follows the same rule: see "Fork Safety for Synchronization Primitives" in
  ``docs/native-code-review.md``.

.. _FS-5:

FS-5 Locks reachable from GC callbacks are reentrant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A lock taken in a ``weakref`` / ``weakref.finalize`` callback or in ``__del__`` must be an
``RLock``. Garbage collection can run the callback on a thread that already holds the lock, for
example while that thread allocates under it, and a plain ``Lock`` then deadlocks the thread
silently.

.. _FS-6:

FS-6 ddtrace threads do not interact with each other
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A periodic thread's target must not acquire a lock that another ddtrace thread also holds, and
must not wait on, start or stop another ddtrace thread. Before a fork, every thread is stopped
and joined in turn, so cross-thread waits turn into deadlocks or into hangs of ``fork()``. The
exceptions are remote configuration enabling or disabling products and the fork machinery
itself (``ThreadRestartTimer``).

.. _FS-7:

FS-7 Blocking calls in thread targets are bounded
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The before-fork hook joins every periodic thread without a timeout, so the application's
``fork()`` waits until the current ``periodic()`` call returns. Every network call, queue read,
lock acquisition or event wait in a thread target needs a timeout.

.. _FS-8:

FS-8 Start threads in the product lifecycle, not on first use (target)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Threads start from the product's ``start()`` / ``enable()`` path, not lazily from an
application thread on its first call (for example, on the first span written). A lazy start on
an application thread can race with a fork in progress on another application thread; the fork
barrier in ``ddtrace/internal/threads.py`` exists only to cover that case. Known exception:
writers that start their worker on the first write. Do not add new ones.

.. _FS-9:

FS-9 Disabled products go dormant (target)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Once started, a ddtrace thread runs until the process exits. Disabling a product, for example
through remote configuration, turns its thread targets into no-ops instead of calling
``stop()`` and later ``start()`` again. Stop/start cycles race with forks and with the restart
policy.

.. _FS-10:

FS-10 Application threads never wait on ddtrace threads
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Code that runs on application threads (instrumentation, tracer API, probes) hands work over
without blocking: ``awake()`` is non-blocking by contract (see ``PeriodicThread_awake`` in
``ddtrace/internal/_threads.cpp``), queues are unbounded or drop on overflow, and nothing calls
``join()`` or waits on an event owned by a ddtrace thread. A blocked application thread can also
be the thread that forks, which deadlocks the fork.

.. _FS-11:

FS-11 ddtrace never forks from its own threads
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ddtrace threads do not call ``os.fork()`` or start ``multiprocessing`` processes with the fork
start method. The fork lock assumes that only application threads fork; ddtrace threads are
joined while it is held.

.. _FS-12:

FS-12 Restart policy and shutdown
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``PeriodicService(autorestart=False)`` or ``__autorestart__ = False`` is allowed only with an
  explicit child re-initialization path that recreates the service. Otherwise the child is left
  with a service that looks running and has no worker.
- ``on_shutdown`` does not run when a thread is stopped for a fork. Do not rely on it to flush
  before a fork.
- In the parent, the restart after a fork is delayed (about 100 ms, coalesced over fork
  storms); in the child it is delayed by the same timer. The delay is a heuristic: it does not
  wait for the child hooks to finish, so a hook that runs longer than that can see workers
  restart while it is still running. Code must not assume a worker is running right after
  ``fork()`` returns.

.. _FS-13:

FS-13 ddtrace threads do not import application-facing stdlib modules
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With module cloning (on when gevent is installed), the stdlib modules imported during ddtrace
startup are dropped and the application imports its own copies. On Python before 3.13, if a
ddtrace thread is the first to import ``threading``, directly or through ``uuid``,
``subprocess``, ``queue``, ``platform``, ``importlib.metadata``, ``concurrent.futures``,
``asyncio`` or ``multiprocessing``, that thread becomes ``threading``'s main thread and the
process hangs at exit. Code that runs on ddtrace threads (thread targets, remote configuration
callbacks, product ``start()``, fork hooks) must use ``ddtrace.internal.threads`` re-exports,
``ddtrace.internal.native.uuid4()``, and ``threads.SimpleQueue``, and must not import those
modules inside functions. The ``ast-grep`` rules ``threading-module-import`` and ``uuid4-usage``
cover the module-level cases.

.. _FS-14:

FS-14 Tests for fork behavior
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A test that forks runs in a subprocess (``@pytest.mark.subprocess``; enforced by
``fork-without-subprocess-marker``). Reproducers start with ``import ddtrace.auto`` on the first
line, the only supported setup. Changes to fork hooks or thread lifecycle need a test that
forks, and preferably one that forks repeatedly from more than one thread.


Layering and Imports
--------------------

ddtrace is split into zones: shared internal code (``internal-core``), integrations
(``contrib``) and products (tracing, appsec, IAST, profiling, debugging, LLMObs, ...).
``scripts/import-analysis/layers.json`` defines the zones and the allowed exceptions. The CI
jobs ``detect_layering_violations`` and ``detect_circular_imports`` compare a PR against its
merge base. The ``dependency-direction-analysis`` and ``circular-import-analysis`` skills explain
how to run them locally and how to fix what they report. The rules below include the ones the
detectors cannot see.

.. _LA-1:

LA-1 Dependencies point from products to shared code
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``internal-core`` and ``contrib`` do not import products.
- Products do not import other products. The exceptions are listed in ``layers.json``
  (currently: contrib and CI Visibility may use tracing).
- Contrib depends on the tracer, not the other way around: integrations import tracing, but
  ``ddtrace/_trace`` and ``ddtrace/trace`` never import contrib. ``layers.py`` does not flag this
  edge, because contrib is not a product zone, so check it by hand. Tracing-side code that needs
  integration-specific values (constants, normalization helpers) gets them from the integration
  through the event payload or a registration, or the code moves to the integration.
  ``ddtrace/_trace/trace_handlers.py`` predates this rule; do not add imports to it.
- Modules not covered by any zone are foundation code, exempt from both rules. That exemption is
  not a place to move code to so that the detector stops reporting it.

When shared code needs product behavior, invert the dependency: the shared side defines a
narrow ``Protocol``, callback or core event, and the product registers its implementation from
its own lifecycle (see ``Product`` in ``ddtrace/internal/products.py``).

.. _LA-2:

LA-2 No new import cycles
~~~~~~~~~~~~~~~~~~~~~~~~~

Fix a cycle by moving code to its owner, extracting the shared contract into a module that
imports neither side, or registering at startup. The ``circular-import-analysis`` skill has the
patterns and examples.

.. _LA-3:

LA-3 Do not hide dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A function-local import or a ``TYPE_CHECKING`` guard that exists to avoid a cycle or a layering
violation hides the dependency without removing it. Deliberate lazy imports are fine when they
exist for startup cost or gevent compatibility. They must say so in a comment, and they must not
cross a forbidden zone boundary.

.. _LA-4:

LA-4 Shared infrastructure knows nothing about its consumers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Generic code used by several products (module watchdogs, code collectors, wrapping, the
``sys.monitoring`` multiplexer, the event hub, writers) does not branch on a product, name a
product's private attributes, or clean up a product's state. When one consumer needs custom
behavior, it overrides or subscribes through its own hook (for example its own
``ModuleWatchdog`` subclass). Shared code manages only the state it owns. The import detectors
miss this, because the dependency is on a name or a convention rather than on an import.

.. _LA-5:

LA-5 Integrations and products meet through core events
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Contrib patches produce events through ``ddtrace.internal.core``; products such as AppSec, IAST
and AI Guard subscribe while they are enabled. Tracing is the exception: integrations are built
on the tracer API, and ``layers.json`` allows contrib to import it (but not the reverse,
`LA-1`_). A contrib patch never imports another product's implementation code or requires that
product to be enabled. Details: ``.cursor/rules/isolated-responsibility.mdc``.

.. _LA-6:

LA-6 Product configuration belongs to the product (target)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Settings for a product live with that product, and shared code or the tracer does not read
another product's settings to decide what to do. The product pushes the configuration it needs
into shared code instead (for example through a registration call or an event). ``layers.json``
currently classifies all of ``ddtrace.internal.settings`` as ``internal-core``, so the detector
does not flag reads such as the tracer importing AppSec settings. Review them by hand and do not
add new ones.

.. _LA-7:

LA-7 Classification and exceptions are explicit decisions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- A new top-level module under ``ddtrace/`` gets a zone or a foundation entry in
  ``layers.json``.
- A product-owned package under ``ddtrace/internal/`` gets its own zone mapping rather than
  falling under the ``ddtrace.internal`` catch-all.
- A new exception in ``layers.json`` is proposed only for a deliberate, lasting dependency, with
  its reason in a ``//`` entry and in the PR description, for a human to accept.

.. _LA-8:

LA-8 Public contracts keep their owner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Do not move public types such as ``Tracer`` or ``Span`` into shared code to satisfy the
detector. Preserve the public import paths (``ddtrace/trace/__init__.py``) and give other
products a narrower interface or an event instead.
