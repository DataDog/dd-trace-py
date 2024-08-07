import abc


class AbsStuff(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def foo(self):
        pass

    @abc.abstractmethod
    def bar(self):
        pass


class ConcrStuff(AbsStuff):
    def foo(self):
        return "foo"

    def bar(self):
        return "bar"
