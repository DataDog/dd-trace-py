import sys

import pytest


PYTHON_VERSION_INFO = sys.version_info


@pytest.mark.subprocess
def test_get_runtime_id():
    from ddtrace.internal import runtime

    runtime_id = runtime.get_runtime_id()
    assert isinstance(runtime_id, str)
    assert runtime_id == runtime.get_runtime_id()
    assert runtime_id == runtime.get_runtime_id()


@pytest.mark.subprocess
def test_get_runtime_id_fork():
    import os

    from ddtrace.internal import runtime

    runtime_id = runtime.get_runtime_id()
    assert isinstance(runtime_id, str)
    assert runtime_id == runtime.get_runtime_id()
    assert runtime_id == runtime.get_runtime_id()

    child = os.fork()

    if child == 0:
        runtime_id_child = runtime.get_runtime_id()
        assert isinstance(runtime_id_child, str)
        assert runtime_id != runtime_id_child
        assert runtime_id != runtime.get_runtime_id()
        assert runtime_id_child == runtime.get_runtime_id()
        assert runtime_id_child == runtime.get_runtime_id()
        os._exit(42)

    pid, status = os.waitpid(child, 0)

    exit_code = os.WEXITSTATUS(status)

    assert exit_code == 42


@pytest.mark.subprocess
def test_get_runtime_id_double_fork():
    import os

    from ddtrace.internal import runtime

    runtime_id = runtime.get_runtime_id()

    child = os.fork()

    if child == 0:
        runtime_id_child = runtime.get_runtime_id()
        assert runtime_id != runtime_id_child

        child2 = os.fork()

        if child2 == 0:
            runtime_id_child2 = runtime.get_runtime_id()
            assert runtime_id != runtime_id_child
            assert runtime_id_child != runtime_id_child2
            os._exit(42)

        pid, status = os.waitpid(child2, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 42

        os._exit(42)

    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42


@pytest.mark.subprocess
def test_ancestor_runtime_id():
    """
    Check that the ancestor runtime ID is set after a fork, and that it remains
    the same in nested forks.
    """
    import os

    from ddtrace.internal import runtime

    ancestor_runtime_id = runtime.get_runtime_id()

    assert ancestor_runtime_id is not None
    assert runtime.get_ancestor_runtime_id() == ancestor_runtime_id

    child = os.fork()

    if child == 0:
        assert ancestor_runtime_id != runtime.get_runtime_id()
        assert ancestor_runtime_id == runtime.get_ancestor_runtime_id()

        child = os.fork()

        if child == 0:
            assert ancestor_runtime_id != runtime.get_runtime_id()
            assert ancestor_runtime_id == runtime.get_ancestor_runtime_id()
            os._exit(42)

        _, status = os.waitpid(child, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 42

        os._exit(42)

    _, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42

    assert runtime.get_ancestor_runtime_id() == ancestor_runtime_id


@pytest.mark.subprocess
def test_parent_and_ancestor_propagated_in_subprocess():
    """Test that parent and ancestor runtime IDs are propagated via env vars in spawned subprocesses."""
    import os
    import subprocess
    import sys

    from ddtrace.internal import runtime

    original_id = runtime.get_runtime_id()

    # Original process has no parent, but ancestor equals self
    assert runtime.get_parent_runtime_id() is None
    assert runtime.get_ancestor_runtime_id() == original_id

    # Manually set env vars to simulate what fork would do
    os.environ["_DD_PY_PARENT_RUNTIME_ID"] = original_id
    os.environ["_DD_PY_ANCESTOR_RUNTIME_ID"] = original_id

    # Spawn a subprocess
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ddtrace.internal import runtime; "
            "import json; "
            "print(json.dumps({"
            "  'runtime_id': runtime.get_runtime_id(), "
            "  'parent_id': runtime.get_parent_runtime_id(), "
            "  'ancestor_id': runtime.get_ancestor_runtime_id()"
            "}))",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    import json

    data = json.loads(result.stdout.strip())

    # Child should have its own runtime ID
    assert data["runtime_id"] != original_id
    # But should inherit parent and ancestor from env vars
    assert data["parent_id"] == original_id
    assert data["ancestor_id"] == original_id


@pytest.mark.subprocess
def test_parent_and_ancestor_propagated_across_forks():
    """Test that parent and ancestor runtime IDs are correctly tracked across multiple forks."""
    import os

    from ddtrace.internal import runtime

    # Generation 0: Original process
    gen0_id = runtime.get_runtime_id()
    assert runtime.get_parent_runtime_id() is None
    assert runtime.get_ancestor_runtime_id() == gen0_id

    # Generation 1: First fork
    child1 = os.fork()

    if child1 == 0:
        gen1_id = runtime.get_runtime_id()

        # Gen1 should have different runtime ID
        assert gen1_id != gen0_id

        # Gen1's parent should be gen0
        assert runtime.get_parent_runtime_id() == gen0_id

        # Gen1's ancestor should be gen0 (first in the chain)
        assert runtime.get_ancestor_runtime_id() == gen0_id

        # Verify env vars are set for potential spawned children
        assert os.environ.get("_DD_PY_PARENT_RUNTIME_ID") == gen0_id
        assert os.environ.get("_DD_PY_ANCESTOR_RUNTIME_ID") == gen0_id

        # Generation 2: Second fork
        child2 = os.fork()

        if child2 == 0:
            gen2_id = runtime.get_runtime_id()

            # Gen2 should have different runtime ID
            assert gen2_id != gen1_id
            assert gen2_id != gen0_id

            # Gen2's parent should be gen1 (immediate parent)
            assert runtime.get_parent_runtime_id() == gen1_id

            # Gen2's ancestor should still be gen0 (root of the chain)
            assert runtime.get_ancestor_runtime_id() == gen0_id

            # Verify env vars are updated
            assert os.environ.get("_DD_PY_PARENT_RUNTIME_ID") == gen1_id
            assert os.environ.get("_DD_PY_ANCESTOR_RUNTIME_ID") == gen0_id

            os._exit(42)

        # Verify env vars are not overriden by forks in the main process
        assert os.environ.get("_DD_PY_PARENT_RUNTIME_ID") == gen0_id
        assert os.environ.get("_DD_PY_ANCESTOR_RUNTIME_ID") == gen0_id

        _, status = os.waitpid(child2, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 42

        os._exit(42)

    _, status = os.waitpid(child1, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42


@pytest.mark.subprocess
@pytest.mark.skipif(
    PYTHON_VERSION_INFO < (3, 14),
    reason="Subinterpreters public API requires Python 3.14+",
)
def test_subinterpreters_have_different_runtime_ids():
    """Test that subinterpreters have different runtime IDs and are independent.

    Subinterpreters are NOT forked processes - they're isolated interpreters
    within the same process. Each should have its own runtime ID with no
    parent/ancestor relationship since no fork occurred.
    """
    import sys

    import subinterpreters

    from ddtrace.internal import runtime

    main_runtime_id = runtime.get_runtime_id()

    interp1 = subinterpreters.create()
    interp2 = subinterpreters.create()

    try:
        # Subinterpreters have isolated sys.path, so we need to extend it
        # to make ddtrace module available for import
        code = """
import sys
sys.path.extend({})
from ddtrace.internal import runtime
(runtime.get_runtime_id(), runtime.get_parent_runtime_id(), runtime.get_ancestor_runtime_id())
""".format(
            repr(sys.path)
        )

        subinterp1_runtime_id, subinterp1_parent_id, subinterp1_ancestor_id = interp1.exec(code)
        subinterp2_runtime_id, subinterp2_parent_id, subinterp2_ancestor_id = interp2.exec(code)

        # Each subinterpreter gets a unique runtime ID
        assert (
            subinterp1_runtime_id != subinterp2_runtime_id
        ), f"Subinterpreters should have different runtime IDs: {subinterp1_runtime_id} == {subinterp2_runtime_id}"
        assert (
            subinterp1_runtime_id != main_runtime_id
        ), f"Subinterpreter 1 should differ from main: {subinterp1_runtime_id} == {main_runtime_id}"
        assert (
            subinterp2_runtime_id != main_runtime_id
        ), f"Subinterpreter 2 should differ from main: {subinterp2_runtime_id} == {main_runtime_id}"

        # Subinterpreters have no parent (no fork occurred)
        assert subinterp1_parent_id is None, f"Subinterpreter 1 parent should be None: {subinterp1_parent_id}"
        assert subinterp2_parent_id is None, f"Subinterpreter 2 parent should be None: {subinterp2_parent_id}"

        # Each subinterpreter is its own root, so ancestor equals self
        assert (
            subinterp1_ancestor_id == subinterp1_runtime_id
        ), f"Subinterpreter 1 ancestor should equal self: {subinterp1_ancestor_id} != {subinterp1_runtime_id}"
        assert (
            subinterp2_ancestor_id == subinterp2_runtime_id
        ), f"Subinterpreter 2 ancestor should equal self: {subinterp2_ancestor_id} != {subinterp2_runtime_id}"

    finally:
        try:
            interp1.close()
        except Exception:
            pass
        try:
            interp2.close()
        except Exception:
            pass
