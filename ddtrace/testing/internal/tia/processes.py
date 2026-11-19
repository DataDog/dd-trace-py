# Adapted from pytest-filemon (Apache-2.0).
"""Conservative process-spawn detection for work outside dd-coverage's collection."""

from __future__ import annotations

import typing as t


_SPAWN_TARGETS = (
    ("subprocess", "Popen.__init__"),
    ("subprocess", "_fork_exec"),
    ("_posixsubprocess", "fork_exec"),
    ("os", "system"),
    ("os", "fork"),
    ("os", "forkpty"),
    ("os", "posix_spawn"),
    ("os", "posix_spawnp"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "execvpe"),
)


class ProcessMonitor:
    def __init__(self) -> None:
        self._undo: list[tuple[t.Any, str, t.Any, t.Any]] = []
        self._active = False
        self._nodeid: t.Optional[str] = None
        self._phase = "import"
        self.spawn_tests: set[str] = set()
        self.spawn_shared = False
        self.valid = True

    @property
    def current_nodeid(self) -> t.Optional[str]:
        return self._nodeid

    @current_nodeid.setter
    def current_nodeid(self, value: t.Optional[str]) -> None:
        self._nodeid = value

    @property
    def phase(self) -> str:
        return self._phase

    @phase.setter
    def phase(self, value: str) -> None:
        self._phase = value

    def activate(self) -> None:
        if not self._active:
            self._install_spawn_wraps()
            self._active = True

    def deactivate(self) -> None:
        self._active = False
        while self._undo:
            owner, name, original, wrapper = self._undo.pop()
            if getattr(owner, name, None) is wrapper:
                setattr(owner, name, original)

    def _install_spawn_wraps(self) -> None:
        for modname, attr in _SPAWN_TARGETS:
            try:
                module = __import__(modname)
            except ImportError:  # pragma: no cover - platform specific
                continue
            owner: t.Any = module
            leaf = attr
            if "." in attr:  # "Popen.__init__"
                cls, _, leaf = attr.partition(".")
                owner = getattr(module, cls, None)
                if owner is None:  # pragma: no cover
                    continue
            orig = getattr(owner, leaf, None)
            if orig is None or getattr(orig, "_tia_spawn_tracking", False):
                continue

            def wrapper(*a: t.Any, __orig: t.Any = orig, **k: t.Any) -> t.Any:
                self._note_spawn()
                return __orig(*a, **k)

            setattr(wrapper, "_tia_spawn_tracking", True)
            try:
                setattr(owner, leaf, wrapper)
            except (AttributeError, TypeError):  # pragma: no cover
                continue
            self._undo.append((owner, leaf, orig, wrapper))

    def _note_spawn(self) -> None:
        """Unknown child dependencies keep the test, or shared fixture users, runnable."""
        if not self._active:
            return
        try:
            if self._phase == "test" and self._nodeid is not None:
                self.spawn_tests.add(self._nodeid)
            elif self._phase in ("collect", "fixture"):
                self.spawn_shared = True
        except Exception:  # pragma: no cover - never break the call we wrap
            self.valid = False
