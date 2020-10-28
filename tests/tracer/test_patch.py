import inspect
from ddtrace.vendor import wrapt



def test_mro():
    x = []

    class A(object):
        def fn(self):
            x.append("A")

    class B(A):
        pass


    class C(A):
        def fn(self):
            x.append("C")
            super(C, self).fn()


    class D(B, C):
        pass


    D().fn()
    assert inspect.getmro(D) == (D, B, C, A)
    assert x == ["C", "A"]

    # wrapt.FunctionWrapper()





