def __specialstuff__(arg):
    return arg


def modulestuff(snafu):
    return snafu


def decorator(f):
    def identity(*args, **kwargs):
        return f(*args, **kwargs)

    return identity


def decoratorwitharg(arg):
    def decorator(f):
        def foo(arg, *args, **kwargs):
            return arg, f(*args, **kwargs)

        return foo

    return decorator


class Stuff(object):
    @staticmethod
    def staticstuff(foo):
        return foo

    @classmethod
    def classstuff(cls):
        return cls

    def instancestuff(self, bar=None):
        return bar

    @property
    def propertystuff(self):
        return -1

    @propertystuff.setter
    def propertystuff(self, value):
        return value

    @decorator
    def decoratedstuff(self):
        return self

    @decorator
    @decorator
    def doublydecoratedstuff(self):
        return None

    @decoratorwitharg(42)
    def decoratedwithargsstuff(self):
        return self

    def nestedstuff(self):
        def localstuff(arg):
            def localerstuff(arg):
                return arg

            return localerstuff(arg)

        return localstuff(self)

    def pointlessstuff(self):
        # TODO: This needs to include the line numbers of its code objects.
        # However, something like this is pretty useless.
        def pointlesslocal(foo):
            return foo or self or pointlesslocal

    def __mangledstuff(self):
        return self

    def generatorstuff(self, n):
        yield "Ready"
        # yield from range(n)
        yield "Done"
        return


class MoreStuff(Stuff):
    def __init__(self):
        self.foo = "foo"

    def hellostuff(self):
        return self.foo


def excstuff():
    try:
        raise Exception("Hello", "world!", 42)
    except Exception:
        pass


alias = modulestuff


class AliasStuff(object):
    def foo(self):
        pass

    bar = foo


def throwexcstuff():
    raise Exception("Hello", "world!", 42)


# TODO: We don't support lambdas for the same reasons we don't support local
# functions.
def lambdastuff():
    return (lambda x: x << 1)(21)


class PropertyStuff(object):
    import operator

    def __init__(self):
        self._foo = "foo"

    foo = property(operator.attrgetter("_foo"))


from time import monotonic_ns  # noqa:E402


def durationstuff(ns):
    end = monotonic_ns() + ns
    while monotonic_ns() < end:
        pass


def mutator(arg):
    arg.append(42)


def age_checker(people, age, name=None):
    return filter(lambda person: person.age > age, people)


def caller(f, *args, **kwargs):
    return f(*args, **kwargs)


def finallystuff():
    a = 0
    try:
        if a == 0:
            Exception("Hello", "world!", 42)
    except Exception:
        return a
    finally:
        a = 42
    return a


class SensitiveData:
    def __init__(self):
        self.password = "foobar"


def sensitive_stuff(pwd):
    token, answer, data = "deadbeef", 42, SensitiveData()  # noqa:F841
    pii_dict = {"jwt": "deadbeef", "password": "hunter2", "username": "admin"}  # noqa:F841
    return pwd


class TestClass():
    def fake_get(self):
        return 5
