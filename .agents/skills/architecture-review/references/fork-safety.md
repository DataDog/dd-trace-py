# Fork safety and background threads

ddtrace runs inside pre-fork servers (Gunicorn, uWSGI), multiprocessing workers and gevent
applications. A fork copies only the forking thread, so any lock, thread or buffer that ddtrace
owns must be in a known state at fork time. The explanation of the machinery is in the
"Fork Safety" section of `ddtrace/internal/README.md`; native primitives are covered by section 2
of `docs/native-code-review.md`. This file lists the rules a change must follow.

Rules marked **target** describe the agreed design for new code. Some existing code predates
them; do not flag unchanged code, but do not let a change extend a known exception.

## Trigger symbols

Apply this checklist when a diff adds or changes any of:

- `os.fork`, `os.register_at_fork`, `multiprocessing`, `forksafe.`, `_before_fork`, `_after_fork`,
  `__autorestart__`, `autorestart=`
- `threading.Thread`, `threading.Timer`, `ThreadPoolExecutor`, `PeriodicService`,
  `AwakeablePeriodicService`, `PeriodicThread`, `threads.Thread`, `periodic.Timer`
- `.start()` / `.stop()` / `.join()` / `.awake()` on a ddtrace service or thread
- `Lock(`, `RLock(`, `Event(`, `Condition(`, `Queue(`, `weakref.finalize`, `__del__`
- `periodic()`, `on_shutdown()`, `reset()`, `flush`, writers, uploaders, remote config callbacks
- Imports inside functions that run on ddtrace threads, fork hooks or remote config callbacks

## Stop conditions

Any of these blocks the change:

1. `os.register_at_fork` called outside `ddtrace/internal/forksafe.py` (FS-1).
2. I/O, queue flushing or network calls in an after-in-child hook (FS-2).
3. A raw `threading.Thread`, `threading.Timer` or executor started by ddtrace code (FS-3).
4. Two ddtrace threads synchronizing with each other, through a shared lock or by one waiting
   on the other (FS-6).
5. A blocking call without a timeout in `periodic()` or another thread target (FS-7).
6. Application code that waits on a ddtrace thread: a blocking `awake()`, `join()` or queue
   handoff on a request path (FS-10).
7. Operating on an inherited lock in the child instead of recreating it (FS-4).

## Rules

### FS-1 Register fork hooks through `forksafe`

Use `forksafe.register_before_fork`, `forksafe.register` (after in child) and
`forksafe.register_after_parent`. `forksafe` is the only caller of `os.register_at_fork`. It
serializes the ddtrace hooks under one lock from before-fork to after-fork, runs them in a
known order relative to thread stop and restart, isolates exceptions, and also runs the child
hooks for uWSGI workers forked at the C level, where `os.register_at_fork` hooks never run.

### FS-2 Hooks are fast, and each registry has one job

- Before fork (parent): quiesce. Do not start threads or acquire locks that other threads may
  hold for a long time.
- After in child: recreate process-local state (locks, buffers, caches, sockets, PIDs). No I/O,
  no flushing: data buffered in the parent belongs to the parent, and flushing it from the child
  duplicates it.
- After in parent: resume work paused before the fork.

Every hook delays the moment the forked child becomes ready, and pre-fork servers kill children
that miss a startup deadline. Process managers such as Celery close inherited file descriptors
after the at-fork callbacks return, so native workers must not be rebuilt while
`forksafe.in_child_hook()` is true. Rely on the delayed restart of `PeriodicThread` instead.

### FS-3 Background work uses ddtrace thread primitives

| Need | Use |
|------|-----|
| Periodic work | `periodic.PeriodicService` / `AwakeablePeriodicService` |
| Periodic work with state reset on fork | `periodic.ForksafeAwakeablePeriodicService` |
| One-shot background task | `threads.Thread` |
| Delayed one-shot callback | `periodic.Timer` |

Only these take part in the fork protocol: they are stopped and joined before the fork and
restarted afterwards. A raw `threading.Thread` keeps running in the parent during the fork,
may hold locks the child inherits, and does not exist in the child. Under gevent, a thread
created through the application's patched `threading` is a greenlet.

### FS-4 Locks and process-local primitives

- Import `Lock`, `RLock`, `Event` and `SimpleQueue` from `ddtrace.internal.threads`. These are
  real OS-thread primitives that gevent does not patch. The ast-grep rules
  `threading-lock-usage` and `threading-module-import` enforce this.
- A lock that must be released in the child is recreated in an after-in-child hook, or created
  with `forksafe.Lock()` / `forksafe.RLock()` / `forksafe.Event()` (a `ResetObject` that is
  replaced after the fork). Never release or clear an inherited lock in the child: the owning
  thread no longer exists.
- Native code follows the same rule: see "Fork Safety for Synchronization Primitives" in
  `docs/native-code-review.md`.

### FS-5 Locks reachable from GC callbacks are reentrant

A lock taken in a `weakref` / `weakref.finalize` callback or in `__del__` must be an `RLock`.
Garbage collection can run the callback on a thread that already holds the lock, for example
while that thread allocates under it, and a plain `Lock` then deadlocks the thread silently.

### FS-6 ddtrace threads do not interact with each other

A periodic thread's target must not acquire a lock that another ddtrace thread also holds, and
must not wait on, start or stop another ddtrace thread. Before a fork, every thread is stopped
and joined in turn, so cross-thread waits turn into deadlocks or into hangs of `fork()`.
The exceptions are remote configuration enabling or disabling products and the fork machinery
itself (`ThreadRestartTimer`).

### FS-7 Blocking calls in thread targets are bounded

The before-fork hook joins every periodic thread without a timeout, so the application's
`fork()` waits until the current `periodic()` call returns. Every network call, queue read,
lock acquisition or event wait in a thread target needs a timeout.

### FS-8 Start threads in the product lifecycle, not on first use (target)

Threads start from the product's `start()` / `enable()` path, not lazily from an application
thread on its first call (for example, on the first span written). A lazy start on an
application thread can race with a fork in progress on another application thread; the fork
barrier in `ddtrace/internal/threads.py` exists only to cover that case. Known exception:
writers that start their worker on the first write. Do not add new ones.

### FS-9 Disabled products go dormant (target)

Once started, a ddtrace thread runs until the process exits. Disabling a product, for example
through remote configuration, turns its thread targets into no-ops instead of calling
`stop()` and later `start()` again. Stop/start cycles race with forks and with the restart
policy.

### FS-10 Application threads never wait on ddtrace threads

Code that runs on application threads (instrumentation, tracer API, probes) hands work over
without blocking: `awake()` is non-blocking by contract (see `PeriodicThread_awake` in
`ddtrace/internal/_threads.cpp`), queues are unbounded or drop on overflow, and nothing calls
`join()` or waits on an event owned by a ddtrace thread. A blocked application thread can also
be the thread that forks, which deadlocks the fork.

### FS-11 ddtrace never forks from its own threads

ddtrace threads do not call `os.fork()` or start `multiprocessing` processes with the fork
start method. The fork lock assumes that only application threads fork; ddtrace threads are
joined while it is held.

### FS-12 Restart policy and shutdown

- `PeriodicService(autorestart=False)` or `__autorestart__ = False` is allowed only with an
  explicit child re-initialization path that recreates the service. Otherwise the child is left
  with a service that looks running and has no worker.
- `on_shutdown` does not run when a thread is stopped for a fork. Do not rely on it to flush
  before a fork.
- In the parent, the restart after a fork is delayed (about 100 ms, coalesced over fork
  storms); in the child it is also delayed until the child hooks are done. Code must not assume
  a worker is running right after `fork()` returns.

### FS-13 ddtrace threads do not import application-facing stdlib modules

With module cloning (on when gevent is installed), the stdlib modules imported during ddtrace
startup are dropped and the application imports its own copies. On Python before 3.13, if a
ddtrace thread is the first to import `threading`, directly or through `uuid`, `subprocess`,
`queue`, `platform`, `importlib.metadata`, `concurrent.futures`, `asyncio` or
`multiprocessing`, that thread becomes `threading`'s main thread and the process hangs at exit.
Code that runs on ddtrace threads (thread targets, remote configuration callbacks, product
`start()`, fork hooks) must use `ddtrace.internal.threads` re-exports,
`ddtrace.internal.native.uuid4()`, and `threads.SimpleQueue`, and must not import those modules
inside functions. The ast-grep rules `threading-module-import` and `uuid4-usage` cover the
module-level cases.

### FS-14 Tests for fork behavior

A test that forks runs in a subprocess (`@pytest.mark.subprocess`; enforced by
`fork-without-subprocess-marker`). Reproducers start with `import ddtrace.auto` on the first
line, the only supported setup. Changes to fork hooks or thread lifecycle need a test that
forks, and preferably one that forks repeatedly from more than one thread.
