import os

from ddtrace.internal import forksafe


state = False


def test_forksafe():
    state = []

    @forksafe.register(after_in_child=lambda: state.append(1))
    def my_func():
        return state

    pid = os.fork()

    if pid == 0:
        # child
        assert my_func() == [1]
        os._exit(12)
    else:
        assert my_func() == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_registry():
    state = []

    @forksafe.register(after_in_child=lambda: state.append(1))
    def f1():
        pass

    @forksafe.register(after_in_child=lambda: state.append(2))
    def f2():
        pass

    @forksafe.register(after_in_child=lambda: state.append(3))
    def f3():
        pass

    forksafe.ddtrace_after_in_child()
    assert state == [1, 2, 3]


def test_after_fork_argument():
    state = []

    @forksafe.register(lambda: state.append(1))
    def fn1(is_in_child_after_fork=False):
        return is_in_child_after_fork

    @forksafe.register(lambda: state.append(2))
    def fn2(is_in_child_after_fork=False):
        return is_in_child_after_fork

    assert fn1() is False
    assert fn2() is False

    pid = os.fork()

    if pid == 0:
        # child
        assert fn1() is True
        # Hooks should have all been fired after the first check.
        assert state == [1, 2]
        assert fn2() is True
        os._exit(12)
    else:
        assert fn1() is False
        assert fn2() is False

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
