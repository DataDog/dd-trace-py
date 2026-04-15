import logging

from ddtrace.internal import forksafe
import ddtrace.internal.native as native


log = logging.getLogger(__name__)


class NativeRuntime:
    """Manages a SharedRuntime with fork-safe lifecycle hooks.

    The SharedRuntime wraps a Tokio async runtime shared across TraceExporter
    instances. This class registers before_fork / after_fork_parent /
    after_fork_child hooks so the runtime is correctly paused and resumed
    around process forks.
    """

    def __init__(self) -> None:
        self._shared_runtime = native.SharedRuntime()
        # Store bound-method references so we can unregister the exact same
        # objects later (list.remove uses __eq__ on bound methods).
        self._before_fork_hook = self._before_fork
        self._after_fork_parent_hook = self._after_fork_parent
        self._after_fork_child_hook = self._after_fork_child
        forksafe.register_before_fork(self._before_fork_hook)
        forksafe.register_after_parent(self._after_fork_parent_hook)
        forksafe.register(self._after_fork_child_hook)

    @property
    def shared_runtime(self) -> native.SharedRuntime:
        return self._shared_runtime

    def _before_fork(self) -> None:
        self._shared_runtime.before_fork()

    def _after_fork_parent(self) -> None:
        self._shared_runtime.after_fork_parent()

    def _after_fork_child(self) -> None:
        self._shared_runtime.after_fork_child()

    def shutdown(self) -> None:
        self._shared_runtime.shutdown()
        forksafe.unregister_before_fork(self._before_fork_hook)
        forksafe.unregister_parent(self._after_fork_parent_hook)
        forksafe.unregister(self._after_fork_child_hook)

    def __del__(self) -> None:
        forksafe.unregister_before_fork(self._before_fork_hook)
        forksafe.unregister_parent(self._after_fork_parent_hook)
        forksafe.unregister(self._after_fork_child_hook)
