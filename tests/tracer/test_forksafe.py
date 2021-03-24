import os

from ddtrace.internal import forksafe


def test_forksafe():
    state = []

    @forksafe.register
    def after_in_child():
        state.append(1)

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

    @forksafe.register
    def after_in_child_1():
        state.append(1)

    def f1():
        pass

    @forksafe.register
    def after_in_child_2():
        state.append(2)

    def f2():
        pass

    @forksafe.register
    def after_in_child_3():
        state.append(3)

    def f3():
        pass

    forksafe.ddtrace_after_in_child()
    assert state == [1, 2, 3]


def test_duplicates():
    state = []

    @forksafe.register
    def hook():
        state.append(1)

    def f1():
        return state

    def f2():
        return state

    def f3():
        return state

    pid = os.fork()

    if pid == 0:
        # child
        assert f1() == f2() == f3() == [1]
        os._exit(12)
    else:
        assert f1() == f2() == f3() == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_method_usage():
    class A:
        def __init__(self):
            self.state = 0
            forksafe.register(self.after_fork)

        def after_fork(self):
            self.state = 1

        def method(self):
            return self.state

    a = A()
    pid = os.fork()
    if pid == 0:
        # child
        assert a.method() == 1
        os._exit(12)
    else:
        assert a.method() == 0

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_hook_exception():
    state = []

    @forksafe.register
    def after_in_child():
        raise ValueError

    @forksafe.register
    def state_append_1():
        forksafe.register(state.append(1))

    @forksafe.register
    def state_append_3():
        forksafe.register(state.append(3))

    def f1():
        return state

    def f2():
        return state

    def f3():
        return state

    pid = os.fork()
    if pid == 0:
        # child
        assert f1() == f2() == f3() == [1, 3]
        os._exit(12)
    else:
        assert f1() == f2() == f3() == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
