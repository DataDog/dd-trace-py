# Removing wrapt from Lock Profiler

Why I did it, how it works, and what we're giving up.

---

## Why Remove wrapt?

Short answer: **We didn't need it.**

The lock profiler only needs to intercept 5 methods and store 8 attributes on locks:
- `acquire()`
- `release()`
- `__enter__()` / `__exit__()` (context manager)
- `locked()` (just query, pass through)

We're using `wrapt.ObjectProxy` - a full-featured transparent proxy with automatic attribute forwarding, `isinstance()` magic, and all sorts of bells and whistles - to wrap... 5 methods.

---

## What Changed

### Before: wrapt.ObjectProxy

- Automatic forwarding of any attribute we don't explicitly handle
- Transparent `isinstance()` checks
- `_self_` prefix convention to avoid attribute name conflicts
- `__getattribute__` interception on every access (overhead!)
- Hidden `__dict__` for `_self_*` attributes (memory!)

```python
import wrapt

class _ProfiledLock(wrapt.ObjectProxy):
    def __init__(self, wrapped, tracer, max_nframes, capture_sampler, endpoint_collection_enabled):
        wrapt.ObjectProxy.__init__(self, wrapped)
        self._self_tracer = tracer                    # _self_ prefix required
        self._self_max_nframes = max_nframes
        self._self_capture_sampler = capture_sampler
        self._self_endpoint_collection_enabled = endpoint_collection_enabled
        self._self_init_loc = "..."
        self._self_acquired_at = 0
        self._self_name = None
    
    def acquire(self, *args, **kwargs):
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)
    
    # ... etc
```

### After: Direct Delegation with __slots__

- Explicit delegation to exactly what we need
- No `__getattribute__` overhead
- `__slots__` for memory efficiency
- Plain Python - easy to read and debug

```python
class _ProfiledLock:
    __slots__ = (
        '__wrapped__',
        '_self_tracer',
        '_self_max_nframes',
        '_self_capture_sampler',
        '_self_endpoint_collection_enabled',
        '_self_init_loc',
        '_self_acquired_at',
        '_self_name',
    )
    
    def __init__(self, wrapped, tracer, max_nframes, capture_sampler, endpoint_collection_enabled):
        self.__wrapped__ = wrapped
        self._self_tracer = tracer
        self._self_max_nframes = max_nframes
        self._self_capture_sampler = capture_sampler
        self._self_endpoint_collection_enabled = endpoint_collection_enabled
        self._self_init_loc = "..."
        self._self_acquired_at = 0
        self._self_name = None
    
    def acquire(self, *args, **kwargs):
        return self._acquire(self.__wrapped__.acquire, *args, **kwargs)
    
    def release(self, *args, **kwargs):
        return self._release(self.__wrapped__.release, *args, **kwargs)
    
    def __enter__(self):
        return self.acquire()
    
    def __exit__(self, *args):
        self.release()
    
    def locked(self):
        return self.__wrapped__.locked()
    
    def __repr__(self):
        return f"<_ProfiledLock({self.__wrapped__!r}) at {self._self_init_loc}>"
```

---

## Technical Details

### 1. The `_LockAllocatorWrapper` Class

When we patch `threading.Lock`, we replace it with our wrapper function. But there's a subtle issue with Python's descriptor protocol:

```python
# Without special handling:
class Foo:
    lock_class = threading.Lock  # Stores our wrapper function

# When accessed:
Foo().lock_class()  # Python binds it as a method, passes self!
# → TypeError: takes no arguments (1 given)
```

The solution is a simple wrapper that implements `__get__` to prevent method binding:

```python
class _LockAllocatorWrapper:
    """Prevents method binding via descriptor protocol."""
    __slots__ = ("_func",)
    
    def __init__(self, func):
        self._func = func
    
    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)
    
    def __get__(self, instance, owner=None):
        return self  # Never bind as a method
```

This is similar to `staticmethod` but as a callable. Much simpler than `wrapt.FunctionWrapper` and does exactly what we need.

**Side effect:** The `__call__` method adds a stack frame, so I had to adjust frame introspection.

### 2. Frame Depth Changes

For profiling, we need to capture the stack trace of where the lock was created. We use `sys._getframe()` to walk up the call stack.

**Old (with wrapt):**
```python
# Had environment-dependent frame depths!
WRAPT_C_EXT = detect_if_c_extension_is_enabled()
frame = sys._getframe(2 if WRAPT_C_EXT else 3)
```

Call stack varied:
```
With C extension:           With pure Python:
Frame 0: __init__          Frame 0: __init__
Frame 1: _profiled_...     Frame 1: _profiled_...
Frame 2: user code ←       Frame 2: wrapt layer ← extra!
                           Frame 3: user code ←
```

**New (without wrapt):**
```python
# Always consistent!
frame = sys._getframe(3)  # Always
```

Call stack is predictable:
```
Frame 0: _ProfiledLock.__init__
Frame 1: _profiled_allocate_lock
Frame 2: _LockAllocatorWrapper.__call__  ← our wrapper
Frame 3: user code (my_lock = threading.Lock()) ← capture this!
```

***note**: If you add or remove a wrapper layer, you'll need to adjust the frame depth.*
No more environment-dependent behavior!

### 3. Why __slots__?

Python objects normally store attributes in a `__dict__`:

```python
class Normal:
    def __init__(self):
        self.attr = value

obj = Normal()
obj.__dict__  # {'attr': value}
```

**Cost:** The dict itself is ~240 bytes minimum, plus each key/value pair.

With `__slots__`, we pre-declare attributes and Python stores them in fixed memory positions:

```python
class Slotted:
    __slots__ = ('attr',)
    def __init__(self):
        self.attr = value

obj = Slotted()
# No __dict__ - attributes stored in slots
```

**Benefit:** Each slot is just 8 bytes (a pointer). For our 8 attributes, that's 64 bytes vs 144+ bytes for a dict.

**Trade-off:** Can't add attributes dynamically. But we never need to - we always have exactly 8 attributes.

---

## Lost Features

**1. Automatic attribute forwarding**
- **wrapt**: `proxy.any_attr` automatically forwards to wrapped object
- **Do we need it?** No - we explicitly delegate the 5 methods we care about
- **How to implement, if needed**
```python
    def __getattr__(self, name):
        return getattr(self.__wrapped__, name)
```

**2. Transparent `isinstance()` checks**
- **wrapt**: `isinstance(proxy, threading.Lock)` returns True
- **Do we need it?** No - user code never sees `_ProfiledLock` directly (we patch at module level)
- **How to implement, if needed**
```python
    def __instancecheck__(self, cls):
        return isinstance(self.__wrapped__, cls)
```

**3. Introspection support** (`dir()`, `vars()`, etc.)
- **wrapt**: `dir(proxy)` shows both proxy and wrapped attributes
- **Do we need it?** No - nobody introspects locks in production
- **How to implement, if needed**
```python
    def __dir__(self):
        return list(set(dir(self.__wrapped__)) | set(self.__slots__))
```

**4. Pickle support**
- **wrapt**: Has special pickle handling
- **Do we need it?** No - locks fundamentally can't be pickled anyway!

**5. Weak reference support**
- **wrapt**: Supports `weakref.ref(proxy)`
- **Do we need it?** No - locks need strong ownership while held

---


## Summary

This refactor removes an external dependency, improves performance, and simplifies the code. No API changes for users - it's purely internal.

**What changed:**
- Replaced `wrapt.ObjectProxy` with direct delegation + `__slots__`
- Added simple `_LockAllocatorWrapper` for descriptor protocol
- Removed wrapt C extension detection (fixed frame depth)
- Updated tests to match new behavior

**Why it's better:**
- 52% less memory per lock (see BENCHMARK_RESULTS.md)
- 10-20% faster operations (see BENCHMARK_RESULTS.md)
- Simpler code (plain Python, no proxy magic)
- Consistent behavior (no C extension variance)
- One less external dependency

**What we're giving up:**
- Automatic attribute forwarding (don't need it)
- Proxy features we never used (see above)

The same approach could probably be applied elsewhere in the profiler, but that's for another time.
