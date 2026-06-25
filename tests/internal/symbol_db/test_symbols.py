import atexit
from importlib.machinery import ModuleSpec
import os
from pathlib import Path
import tempfile
from types import ModuleType
import typing as t
from unittest import mock

import pytest

from ddtrace.internal.symbol_db.symbols import Scope
from ddtrace.internal.symbol_db.symbols import ScopeContext
from ddtrace.internal.symbol_db.symbols import ScopeData
from ddtrace.internal.symbol_db.symbols import ScopeType
from ddtrace.internal.symbol_db.symbols import Symbol
from ddtrace.internal.symbol_db.symbols import SymbolType
from ddtrace.internal.symbol_db.symbols import _line_ranges


@pytest.fixture(autouse=True, scope="function")
def pid_file_teardown():
    from ddtrace.internal.ipc import SharedStringFile
    from ddtrace.internal.symbol_db.remoteconfig import shared_pid_file

    yield

    shared_pid_file.clear()
    # pytest.mark.subprocess tests run their body in a child process, where
    # shared_pid_file is keyed by os.getppid() -- i.e. this worker's own pid, not
    # its ppid used above. Clear that file too, or pids leak across subprocess
    # tests scheduled on the same pytest-xdist worker.
    SharedStringFile(f"{os.getpid()}-symdb-pids").clear()


def test_symbol_from_code():
    def foo(a, b, c=None):
        loc = 42
        return loc

    symbols = Symbol.from_code(foo.__code__)
    assert {s.name for s in symbols if s.symbol_type == SymbolType.ARG} == {"a", "b", "c"}
    assert {s.name for s in symbols if s.symbol_type == SymbolType.LOCAL} == {"loc"}


def test_symbols_class():
    class Sup:
        pass

    class Sym(Sup):
        def __init__(self):
            self._foo = "foo"

        @property
        def foo(self):
            return self._foo

        @foo.setter
        def _(self, value):
            self._foo = value

        @classmethod
        def bar(cls):
            pass

        @staticmethod
        def baz():
            pass

        def gen(n: int = 10, _untyped=None) -> t.Generator[int, None, None]:  # type: ignore[misc]
            yield from range(n)

        async def coro(b):
            oroc = 42
            yield oroc

        def me(self) -> "Sym":
            return self

    module = ModuleType("test")
    module.Sym = Sym
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    (class_scope,) = scope.scopes
    assert class_scope.name == "tests.internal.symbol_db.test_symbols.test_symbols_class.<locals>.Sym"

    assert class_scope.language_specifics == {
        "super_classes": ["tests.internal.symbol_db.test_symbols.test_symbols_class.<locals>.Sup"]
    }

    (field,) = (s for s in class_scope.symbols if s.symbol_type == SymbolType.FIELD)
    assert field.name == "_foo"

    assert {s.name for s in class_scope.scopes if s.scope_type == ScopeType.FUNCTION} == {
        "__init__",
        "bar",
        "baz",
        "coro",
        "foo",
        "gen",
        "me",
    }

    gen_scope = next(_ for _ in class_scope.scopes if _.name == "gen")
    assert gen_scope.language_specifics == {
        "return_type": "typing.Generator[int, NoneType, NoneType]",
        "function_type": "generator",
    }
    gen_line = Sym.gen.__code__.co_firstlineno + 1
    assert gen_scope.symbols == [
        Symbol(symbol_type=SymbolType.ARG, name="n", line=gen_line, type="int"),
        Symbol(symbol_type=SymbolType.ARG, name="_untyped", line=gen_line, type=None),
    ]

    assert next(_ for _ in class_scope.scopes if _.name == "foo").language_specifics == {"method_type": "property"}

    assert next(_ for _ in class_scope.scopes if _.name == "bar").language_specifics == {"method_type": "class"}

    assert next(_ for _ in class_scope.scopes if _.name == "me").language_specifics == {"return_type": "Sym"}


def test_symbols_decorators():
    """Test that we get the undecorated functions from a module scope."""

    def deco(f):
        return f

    @deco
    def foo():
        pass

    module = ModuleType("test")
    module.foo = foo
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    (foo_scope,) = scope.scopes
    assert foo_scope.name == "foo"


def test_symbols_decorators_included():
    def deco(f):
        return f

    @deco
    def foo():
        pass

    module = ModuleType("test")
    module.deco = deco
    module.foo = foo
    module.__spec__ = ModuleSpec("test", None)
    module.__spec__.origin = __file__

    scope = Scope.from_module(module)

    assert {_.name for _ in scope.scopes} == {"foo", "deco"}


def test_symbols_decorated_methods():
    """Test that we get the undecorated class methods."""

    def method_decorator(f):
        def _(self, *args, **kwargs):
            return f(self, *args, **kwargs)

        return _

    class Foo:
        @method_decorator
        def bar(self):
            pass

    scope = Scope._get_from(Foo, ScopeData(Path(__file__), set()))
    (bar_scope,) = scope.scopes
    assert bar_scope.name == "bar"


def test_symbols_to_json():
    assert Scope(
        scope_type=ScopeType.MODULE,
        name="test",
        source_file=__file__,
        start_line=0,
        end_line=0,
        symbols=[
            Symbol(
                symbol_type=SymbolType.STATIC_FIELD,
                name="foo",
                line=0,
            ),
        ],
        scopes=[],
    ).to_json() == {
        "scope_type": ScopeType.MODULE,
        "name": "test",
        "source_file": __file__,
        "start_line": 0,
        "end_line": 0,
        "symbols": [
            {
                "symbol_type": SymbolType.STATIC_FIELD,
                "name": "foo",
                "line": 0,
                "type": None,
            }
        ],
        "scopes": [],
        "injectible_lines": [],
        "has_injectible_lines": False,
        "language_specifics": {},
    }


@pytest.mark.parametrize(
    "file_size,num_attributes",
    [
        (1000, 5),
        (10_000, 50),
        (100_000, 200),
        (1_000_000, 1000),
    ],
)
def test_benchmark_module_get_from(benchmark, file_size, num_attributes):
    """Benchmark performance of Scope._get_from with modules of different complexities."""
    # Create a module with the specified number of attributes
    module_name = f"test_module_{num_attributes}"
    test_module = ModuleType(module_name)
    test_module.__spec__ = ModuleSpec(module_name, None)

    # Create temp files with the specified size
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    for i in range(file_size):
        temp_file.write(b"0")
    temp_file.close()
    test_module.__spec__.origin = temp_file.name

    # Register cleanup to delete temp file after test
    atexit.register(lambda: os.unlink(temp_file.name))

    # Add attributes
    for i in range(num_attributes):
        setattr(test_module, f"attr_{i}", f"value_{i}")

    # Define a wrapper function that creates a fresh ScopeData object for each benchmark run
    def benchmark_wrapper():
        data = ScopeData(Path(__file__), set())
        result = Scope._get_from(test_module, data)
        return result

    # Run the benchmark
    result = benchmark(benchmark_wrapper)

    # Verify results
    assert result is not None
    assert result.scope_type == ScopeType.MODULE
    assert result.name == module_name

    # Check that our custom attributes are in the symbols
    attr_names = {symbol.name for symbol in result.symbols}
    for i in range(num_attributes):
        assert f"attr_{i}" in attr_names

    assert result.language_specifics["file_hash"] != ""


@pytest.mark.parametrize(
    "lines,expected",
    [
        # Empty input
        (set(), []),
        # Single line
        ({5}, [{"start": 5, "end": 5}]),
        # Single contiguous range
        ({1, 2, 3, 4, 5}, [{"start": 1, "end": 5}]),
        # Two disjoint ranges
        ({1, 2, 3, 7, 8, 9}, [{"start": 1, "end": 3}, {"start": 7, "end": 9}]),
        # Multiple single-line ranges (no contiguous pairs)
        (
            {1, 3, 5, 7},
            [{"start": 1, "end": 1}, {"start": 3, "end": 3}, {"start": 5, "end": 5}, {"start": 7, "end": 7}],
        ),
        # Mixed: single lines and a run
        ({10, 20, 21, 22, 30}, [{"start": 10, "end": 10}, {"start": 20, "end": 22}, {"start": 30, "end": 30}]),
        # Range starting at line 1
        ({1, 2, 4, 5, 6}, [{"start": 1, "end": 2}, {"start": 4, "end": 6}]),
    ],
)
def test_symbols_injectible_line_ranges(lines, expected):
    assert _line_ranges(lines) == expected


def test_scope_context_upload_skips_empty_batch():
    """Empty scope batches must not produce a SymDB upload request."""
    context = ScopeContext()

    with mock.patch("ddtrace.internal.symbol_db.symbols.connector") as mock_connector:
        with context._scopes_lock:
            context._upload_locked()

    mock_connector.assert_not_called()


def test_scope_context_upload_metadata():
    """ScopeContext exposes the upload metadata fields on the event envelope,
    and _upload_locked populates per-batch fields on both the event and the
    attachment payload, advancing batchNum across uploads.
    """
    import gzip
    import json
    from unittest import mock

    from ddtrace.internal.symbol_db.symbols import ScopeContext

    def make_scope(name: str) -> Scope:
        return Scope(
            scope_type=ScopeType.MODULE,
            name=name,
            source_file=__file__,
            start_line=0,
            end_line=0,
            symbols=[],
            scopes=[],
        )

    ctx = ScopeContext()
    expected_upload_id = ctx._upload_id

    # Static fields on the event envelope, set at construction time.
    assert ctx._event_data["ddsource"] == "python"
    assert ctx._event_data["type"] == "symdb"
    assert ctx._event_data["language"] == "python"
    assert "runtimeId" in ctx._event_data
    assert "parentId" in ctx._event_data
    assert ctx._event_data["uploadId"] == expected_upload_id
    assert ctx._event_data["final"] is False

    captured = {}
    real_compress = gzip.compress

    def capturing_compress(data, *args, **kwargs):
        captured["bytes"] = data
        return real_compress(data, *args, **kwargs)

    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_conn = mock.MagicMock()
    mock_conn.getresponse.return_value = mock_response

    with (
        mock.patch("ddtrace.internal.symbol_db.symbols.connector") as connector_mock,
        mock.patch("ddtrace.internal.symbol_db.symbols.gzip.compress", side_effect=capturing_compress),
    ):
        connector_mock.return_value.return_value.__enter__.return_value = mock_conn

        # First upload: batchNum starts at 1 and the attachment carries the
        # same upload metadata as the event envelope.
        with ctx._scopes_lock:
            ctx._scopes.append(make_scope("first"))
            ctx._upload_locked()

        assert ctx._event_data["uploadId"] == expected_upload_id
        assert ctx._event_data["batchNum"] == 1
        assert ctx._event_data["attachmentSize"] > 0

        attachment = json.loads(captured["bytes"].decode("utf-8"))
        assert attachment["upload_id"] == expected_upload_id
        assert attachment["batch_num"] == 1
        assert attachment["final"] is False

        # Second upload: batchNum must increment on both the event envelope
        # and the attachment payload.
        with ctx._scopes_lock:
            ctx._scopes.append(make_scope("second"))
            ctx._upload_locked()

        assert ctx._event_data["batchNum"] == 2
        attachment = json.loads(captured["bytes"].decode("utf-8"))
        assert attachment["batch_num"] == 2


@pytest.mark.subprocess(ddtrace_run=True, env=dict(DD_SYMBOL_DATABASE_UPLOAD_ENABLED="1"))
def test_symbols_upload_enabled():
    from ddtrace.internal.native import RemoteConfigProduct
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    assert not SymbolDatabaseUploader.is_installed()
    assert remoteconfig_poller.get_registered(RemoteConfigProduct.LiveDebuggerSymbolDb) is not None


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_SYMBOL_DATABASE_INCLUDES="tests.submod.stuff,tests.submod.traced_stuff"),
    err=None,
)
def test_symbols_force_upload():
    import typing as t

    from ddtrace.internal.symbol_db.symbols import ScopeType
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    SymbolDatabaseUploader.install()

    context = t.cast(SymbolDatabaseUploader, SymbolDatabaseUploader._instance)._context

    def get_scope(name: str) -> t.Optional[dict]:
        for scope in context.to_json()["scopes"]:
            if scope["name"] == name:
                return t.cast(dict[str, t.Any], scope)
        return None

    for name in ("tests.submod.stuff", "tests.submod.traced_stuff"):
        assert get_scope(name) is None

    import tests.submod.stuff  # noqa
    import tests.submod.traced_stuff  # noqa

    for name in ("tests.submod.stuff", "tests.submod.traced_stuff"):
        assert (scope := get_scope(name)) is not None
        assert scope["scope_type"] == ScopeType.MODULE
        assert scope["name"] == name


@pytest.mark.subprocess(
    ddtrace_run=True, env=dict(DD_SYMBOL_DATABASE_INCLUDES="tests.submod.stuff,tests.submod.traced_stuff"), err=None
)
def test_symbols_timeout_upload():
    from time import sleep
    import typing as t

    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    SymbolDatabaseUploader.install()
    assert SymbolDatabaseUploader.is_installed()

    context = t.cast(SymbolDatabaseUploader, SymbolDatabaseUploader._instance)._context

    import tests.submod.stuff  # noqa
    import tests.submod.traced_stuff  # noqa

    for _ in range(5):
        if not context:
            break
        sleep(1)
    else:
        raise AssertionError("Symbols not uploaded")


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_symbols_fork_uploads():
    """
    Test that we disable Symbol DB on processes that are not the main one nor
    the first fork child.

    In the first fork child, where SymDB stays enabled, also verify that the
    ScopeContext's upload metadata (uploadId, runtimeId, parentId) is
    refreshed by the post-fork hook.
    """
    import os
    import typing as t

    from ddtrace.internal import forksafe
    from ddtrace.internal.remoteconfig import ConfigMetadata
    from ddtrace.internal.remoteconfig import Payload
    from ddtrace.internal.runtime import get_ancestor_runtime_id
    from ddtrace.internal.runtime import get_runtime_id
    from ddtrace.internal.symbol_db.remoteconfig import _rc_callback
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    SymbolDatabaseUploader.install()

    parent_runtime_id = get_runtime_id()
    parent_context = t.cast(SymbolDatabaseUploader, SymbolDatabaseUploader._instance)._context
    parent_upload_id = parent_context._upload_id

    pids = []
    rc_data = [Payload(ConfigMetadata("test", "symdb", "hash", 0, 0), "test", None)]

    # Fork 10 children from the same parent. In each child, re-run the RC
    # callback and verify the install/uninstall invariant (SymDB stays
    # enabled only in the first fork child) and, where applicable, the
    # post-fork ScopeContext metadata refresh.
    for _ in range(10):
        if pid := os.fork():
            # Parent: record the child's pid and move on to the next fork.
            pids.append(pid)
            continue

        # Child path.
        try:
            # We're in a forked child, so the ancestor runtime id must
            # be set.
            assert get_ancestor_runtime_id() is not None
            # Call the RC callback multiple times to check for stability
            for i in range(10):
                _rc_callback(rc_data)
                # SymDB stays enabled in the first fork child and is
                # disabled in any other (subsequent or deeper) fork
                # child to avoid duplicate uploads.
                #
                # forksafe._forked flips to True in the parent via an
                # after_in_parent hook on the first fork, so children #2..N
                # of the same parent inherit _forked=True via the fork's
                # memory copy. Only the first child observes False here.
                first_child = not forksafe.has_forked()
                assert SymbolDatabaseUploader.is_installed() == first_child, f"iteration {i} is stable"

            if SymbolDatabaseUploader.is_installed():
                # First fork child: the ScopeContext forksafe hook must
                # have refreshed the upload metadata to reflect this
                # child's runtime rather than the parent's.
                child_context = t.cast(SymbolDatabaseUploader, SymbolDatabaseUploader._instance)._context
                assert child_context._event_data["runtimeId"] == get_runtime_id()
                assert child_context._event_data["runtimeId"] != parent_runtime_id
                assert child_context._event_data["parentId"] == parent_runtime_id
                assert child_context._upload_id != parent_upload_id
                assert child_context._event_data["uploadId"] == child_context._upload_id
                assert child_context._batch_counter == 0
        except BaseException:
            os._exit(1)
        os._exit(0)

    for pid in pids:
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, f"child {pid} exited with status {status}"


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_symbols_fork_forces_reenable_and_install():
    """
    Fork force-re-enables SymDB regardless of a prior disable, since
    ProductManager.restart_products runs on every fork child. The first fork
    child legitimately installs, but a generation-2+ descendant must stay
    blocked by the get_generation() > 1 guard, even though the pid file looks
    empty from its own point of view.

    Subprocess test: mutates global singleton state and calls os.fork()
    directly, and needs ddtrace_run=True to register the real forksafe hooks
    under test.
    """
    import os
    from unittest import mock

    from ddtrace.internal.remoteconfig import ConfigMetadata
    from ddtrace.internal.remoteconfig import Payload
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
    from ddtrace.internal.symbol_db.remoteconfig import _rc_callback
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    # Simulate that this process was already correctly disabled by the guard,
    # e.g. some ancestor already decided this branch shouldn't upload symbols.
    remoteconfig_poller.unregister_callback("LIVE_DEBUGGING_SYMBOL_DB")
    remoteconfig_poller.disable_product("LIVE_DEBUGGING_SYMBOL_DB")
    assert remoteconfig_poller.get_registered("LIVE_DEBUGGING_SYMBOL_DB") is None

    upload_payload = [Payload(ConfigMetadata("test", "symdb", "hash", 0, 0), "test", {"upload_symbols": True})]

    with mock.patch.object(SymbolDatabaseUploader, "install", wraps=SymbolDatabaseUploader.install) as mock_install:
        if not (child := os.fork()):
            # The after-fork hook (ProductManager.restart_products -> symbol_db.restart())
            # has already run by this point, before any of our own code executes here.
            assert remoteconfig_poller.get_registered("LIVE_DEBUGGING_SYMBOL_DB") is not None, (
                "fork should have force re-registered the RC callback despite the earlier disable"
            )

            # Nothing has polluted the shared pid file yet from this branch's point of
            # view, so the guard doesn't trip and SymDB actually installs -- proving
            # install() runs in a fork child that had just been explicitly disabled in
            # its parent moments before the fork.
            _rc_callback(upload_payload)
            assert SymbolDatabaseUploader.is_installed()
            assert mock_install.call_count == 1

            # This child spawns a sub-worker of its own. The grandchild is two fork
            # generations removed from the original process, so forksafe.get_generation()
            # is 2 there and the guard's "generation > 1" clause must block it outright,
            # regardless of the (still-empty, from the grandchild's point of view) shared
            # pid file. Note that neither SymbolDatabaseUploader.is_installed() nor
            # mock_install.call_count are reliable signals of what happens *in* the
            # grandchild: both are inherited via copy-on-write from the already-installed
            # child (call_count is already 1 before the grandchild runs any code of its
            # own), so a fresh install() call in the grandchild is only visible as an
            # *increment* from that inherited baseline, reported back through a pipe since
            # mock state doesn't cross the fork boundary any other way.
            baseline_install_calls = mock_install.call_count
            r, w = os.pipe()
            if not (grandchild := os.fork()):
                os.close(r)
                _rc_callback(upload_payload)
                os.write(w, str(mock_install.call_count).encode())
                os.close(w)
                os._exit(0)
            os.close(w)
            grandchild_install_calls = int(os.read(r, 8))
            os.close(r)
            _, status = os.waitpid(grandchild, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, f"grandchild failed with status {status}"
            assert grandchild_install_calls == baseline_install_calls, (
                "install() must not run in a generation-2+ fork descendant, even though the "
                "forced re-enablement fires there too"
            )

            # Second delivery to this *same* child: the grandchild's pid is now on the
            # shared pid file, so the guard trips and uninstalls SymDB again -- even
            # though this process had legitimately installed it moments ago in
            # response to the same forced re-enablement.
            _rc_callback(upload_payload)
            assert not SymbolDatabaseUploader.is_installed()
            assert mock_install.call_count == 1, "install() should not run again once disabled"

            os._exit(0)

        _, status = os.waitpid(child, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, f"child failed with status {status}"


@pytest.mark.subprocess(ddtrace_run=True, err=None)
def test_symbols_fork_sibling_workers_installs_at_most_twice():
    """
    The guard assumes a single long-lived parent forking worker children
    directly (e.g. a prefork server recycling workers one at a time), not
    workers that go on to fork their own descendants. Confirm SymDB installs
    in at most two processes total -- the parent and the first forked worker
    -- with every later sibling blocked, even after worker recycling.

    Subprocess test for the same reasons as
    test_symbols_fork_forces_reenable_and_install.
    """
    import os
    from unittest import mock

    from ddtrace.internal.remoteconfig import ConfigMetadata
    from ddtrace.internal.remoteconfig import Payload
    from ddtrace.internal.symbol_db.remoteconfig import _rc_callback
    from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

    upload_payload = [Payload(ConfigMetadata("test", "symdb", "hash", 0, 0), "test", {"upload_symbols": True})]

    with mock.patch.object(SymbolDatabaseUploader, "install", wraps=SymbolDatabaseUploader.install) as mock_install:
        # The parent installs first, as in the common case where SymDB is already
        # enabled in the main process before it starts forking worker children.
        _rc_callback(upload_payload)
        assert SymbolDatabaseUploader.is_installed()
        assert mock_install.call_count == 1

        NUM_WORKERS = 4
        install_flags = []
        for _ in range(NUM_WORKERS):
            r, w = os.pipe()
            if not (worker := os.fork()):
                os.close(r)
                _rc_callback(upload_payload)
                os.write(w, b"1" if SymbolDatabaseUploader.is_installed() else b"0")
                os.close(w)
                os._exit(0)
            os.close(w)
            install_flags.append(os.read(r, 1))
            os.close(r)
            _, status = os.waitpid(worker, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, f"worker failed with status {status}"

        installed_count = install_flags.count(b"1")
        assert installed_count == 1, (
            f"expected exactly one of {NUM_WORKERS} sequentially-forked sibling workers to install SymDB, "
            f"got {installed_count}: {install_flags}"
        )


@pytest.mark.subprocess(run_module=True, err=None)
def test_symbols_spawn_uploads():
    def spawn_target(results):
        from ddtrace.internal.remoteconfig import ConfigMetadata
        from ddtrace.internal.remoteconfig import Payload
        from ddtrace.internal.symbol_db.remoteconfig import _rc_callback
        from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader

        SymbolDatabaseUploader.install()

        rc_data = [Payload(ConfigMetadata("test", "symdb", "hash", 0, 0), "test", None)]
        _rc_callback(rc_data)
        results.append(SymbolDatabaseUploader.is_installed())

    if __name__ == "__main__":
        import multiprocessing

        multiprocessing.freeze_support()

        multiprocessing.set_start_method("spawn", force=True)
        mc_context = multiprocessing.get_context("spawn")
        manager = multiprocessing.Manager()
        returns = manager.list()
        jobs = []

        for _ in range(10):
            p = mc_context.Process(target=spawn_target, args=(returns,))
            p.start()
            jobs.append(p)

        for p in jobs:
            p.join()

        assert sum(returns) == 1, returns
