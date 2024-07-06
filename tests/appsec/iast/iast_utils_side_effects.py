class MagicMethodsException:
    _data = "abc"

    def join(self, iterable):
        return self._data + "".join(iterable)

    def encode(self, *args, **kwargs):
        return self._data.encode(*args, **kwargs)

    def decode(self, *args, **kwargs):
        return self._data.decode(*args, **kwargs)

    def split(self, *args, **kwargs):
        return self._data.split(*args, **kwargs)

    def extend(self, a):
        ba = bytearray(self._data, encoding="utf-8")
        ba.extend(a)
        return ba

    def ljust(self, width, fill_char=" "):
        return self._data.ljust(width, fill_char)

    def zfill(self, width):
        return self._data.zfill(width)

    def format(self, *args, **kwargs):
        return self._data.format(*args, **kwargs)

    def format_map(self, *args, **kwargs):
        return self._data.format_map(*args, **kwargs)

    def upper(self, *args, **kwargs):
        return self._data

    def lower(self, *args, **kwargs):
        return self._data

    def replace(self, *args, **kwargs):
        return self._data.replace(*args, **kwargs)

    def swapcase(self, *args, **kwargs):
        return self._data

    def title(self, *args, **kwargs):
        return self._data

    def capitalize(self, *args, **kwargs):
        return self._data

    def casefold(self, *args, **kwargs):
        return self._data

    def translate(self, *args, **kwargs):
        return self._data

    def __init__(self, data):
        self._data = data

    # We need those magic methods to verify the test with assert result = XXX
    # def __new__(cls, *args, **kwargs):
    #     return cls
    # def __setattr__(self, name, value):
    #     raise Exception("side effect")
    # def __getattribute__(self, name):
    #     raise Exception("side effect")
    # def __getattr__(self, name):
    #     raise Exception("side effect")

    def __repr__(self):
        raise Exception("side effect")

    def __str__(self):
        raise Exception("side effect")

    def __delattr__(self, name):
        raise Exception("side effect")

    def __getitem__(self, key):
        raise Exception("side effect")

    def __setitem__(self, key, value):
        raise Exception("side effect")

    def __delitem__(self, key):
        raise Exception("side effect")

    def __iter__(self):
        raise Exception("side effect")

    def __next__(self):
        raise Exception("side effect")

    def __contains__(self, item):
        raise Exception("side effect")

    def __len__(self):
        raise Exception("side effect")

    def __call__(self, *args, **kwargs):
        raise Exception("side effect")

    def __enter__(self):
        raise Exception("side effect")

    def __exit__(self, exc_type, exc_value, traceback):
        raise Exception("side effect")

    def __await__(self):
        raise Exception("side effect")

    def __aiter__(self):
        raise Exception("side effect")

    async def __anext__(self):
        raise Exception("side effect")

    def __bytes__(self):
        raise Exception("side effect")

    def __format__(self, format_spec):
        raise Exception("side effect")

    def __lt__(self, other):
        raise Exception("side effect")

    def __le__(self, other):
        raise Exception("side effect")

    def __eq__(self, other):
        raise Exception("side effect")

    def __ne__(self, other):
        raise Exception("side effect")

    def __gt__(self, other):
        raise Exception("side effect")

    def __ge__(self, other):
        raise Exception("side effect")

    def __hash__(self):
        raise Exception("side effect")

    def __bool__(self):
        raise Exception("side effect")

    def __reversed__(self):
        raise Exception("side effect")

    def __abs__(self):
        raise Exception("side effect")

    def __neg__(self):
        raise Exception("side effect")

    def __pos__(self):
        raise Exception("side effect")

    def __invert__(self):
        raise Exception("side effect")

    def __round__(self, n=None):
        raise Exception("side effect")

    def __floor__(self):
        raise Exception("side effect")

    def __ceil__(self):
        raise Exception("side effect")

    def __trunc__(self):
        raise Exception("side effect")

    def __sub__(self, other):
        raise Exception("side effect")

    def __mul__(self, other):
        raise Exception("side effect")

    def __matmul__(self, other):
        raise Exception("side effect")

    def __truediv__(self, other):
        raise Exception("side effect")

    def __floordiv__(self, other):
        raise Exception("side effect")

    def __mod__(self, other):
        raise Exception("side effect")

    def __divmod__(self, other):
        raise Exception("side effect")

    def __pow__(self, other, modulo=None):
        raise Exception("side effect")

    def __lshift__(self, other):
        raise Exception("side effect")

    def __rshift__(self, other):
        raise Exception("side effect")

    def __and__(self, other):
        raise Exception("side effect")

    def __xor__(self, other):
        raise Exception("side effect")

    def __or__(self, other):
        raise Exception("side effect")

    def __radd__(self, other):
        raise Exception("side effect")

    def __rsub__(self, other):
        raise Exception("side effect")

    def __rmul__(self, other):
        raise Exception("side effect")

    def __rmatmul__(self, other):
        raise Exception("side effect")

    def __rtruediv__(self, other):
        raise Exception("side effect")

    def __rfloordiv__(self, other):
        raise Exception("side effect")

    def __rmod__(self, other):
        raise Exception("side effect")

    def __rdivmod__(self, other):
        raise Exception("side effect")

    def __rpow__(self, other):
        raise Exception("side effect")

    def __rlshift__(self, other):
        raise Exception("side effect")

    def __rrshift__(self, other):
        raise Exception("side effect")

    def __rand__(self, other):
        raise Exception("side effect")

    def __rxor__(self, other):
        raise Exception("side effect")

    def __ror__(self, other):
        raise Exception("side effect")

    def __iadd__(self, other):
        raise Exception("side effect")

    def __isub__(self, other):
        raise Exception("side effect")

    def __imul__(self, other):
        raise Exception("side effect")

    def __imatmul__(self, other):
        raise Exception("side effect")

    def __itruediv__(self, other):
        raise Exception("side effect")

    def __ifloordiv__(self, other):
        raise Exception("side effect")

    def __imod__(self, other):
        raise Exception("side effect")

    def __ipow__(self, other):
        raise Exception("side effect")

    def __ilshift__(self, other):
        raise Exception("side effect")

    def __irshift__(self, other):
        raise Exception("side effect")

    def __iand__(self, other):
        raise Exception("side effect")

    def __ixor__(self, other):
        raise Exception("side effect")

    def __ior__(self, other):
        raise Exception("side effect")

    def __dir__(self):
        raise Exception("side effect")

    def __copy__(self):
        raise Exception("side effect")

    def __deepcopy__(self, memo):
        raise Exception("side effect")

    def __subclasshook__(self, subclass):
        raise Exception("side effect")

    def __instancecheck__(self, instance):
        raise Exception("side effect")

    def __subclasscheck__(self, subclass):
        raise Exception("side effect")

    def __prepare__(cls, name, bases):
        raise Exception("side effect")

    def __class_getitem__(self, item):
        raise Exception("side effect")

    def __subclass_prepare__(self, cls):
        raise Exception("side effect")

    def __init_subclass__(self, *args, **kwargs):
        raise Exception("side effect")

    def __length_hint__(self):
        raise Exception("side effect")

    def __missing__(self, key):
        raise Exception("side effect")

    def __complex__(self):
        raise Exception("side effect")

    def __int__(self):
        raise Exception("side effect")

    def __float__(self):
        raise Exception("side effect")

    def __index__(self):
        raise Exception("side effect")

    def __del__(self):
        raise Exception("side effect")

    def __getnewargs_ex__(self):
        raise Exception("side effect")

    def __getnewargs__(self):
        raise Exception("side effect")

    def __setstate__(self, state):
        raise Exception("side effect")

    def __dict__(self):
        raise Exception("side effect")

    def __weakref__(self):
        raise Exception("side effect")
