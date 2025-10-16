<p align="center">
  <img align="center" src="art/logo.png" height="320px" />
</p>

<h1 align="center">Echion</h1>

<p align="center">
Near-zero-overhead, in-process CPython frame stack sampler with async support
</p>


## Synopsis

Echion is an in-process CPython frame stack sampler. It can achieve
near-zero-overhead, similar to [Austin][austin], by sampling the frame stack of
each thread without holding the GIL. Native stacks can be sampled too, but the
overhead is higher.

Echion is also the first example of a high-performance sampling async profiler
for CPython.


## Installation

Currently Echion is available to install from PyPI with

```console
pip install echion
```

Alternativey, if a wheel is not available for your combination of platform and
architecture, it can be installed from sources with

```console
pip install git+https://github.com/p403n1x87/echion
```

Compilation requires a C++ compiler and static versions of the `libunwind` and
`lzma` libraries.


## Usage

The following is the output of the `echion --help` command.

```
usage: echion [-h] [-i INTERVAL] [-c] [-n] [-o OUTPUT] [-s] [-w] [-v] [-V] ...

In-process CPython frame stack sampler

positional arguments:
  command               Command string to execute.

options:
  -h, --help            show this help message and exit
  -i INTERVAL, --interval INTERVAL
                        sampling interval in microseconds
  -c, --cpu             sample on-CPU stacks only
  -x EXPOSURE, --exposure EXPOSURE
                        exposure time, in seconds
  -m, --memory          Collect memory allocation events
  -n, --native          sample native stacks
  -o OUTPUT, --output OUTPUT
                        output location (can use %(pid) to insert the process ID)
  -p PID, --pid PID     Attach to the process with the given PID
  -s, --stealth         stealth mode (sampler thread is not accounted for)
  -w WHERE, --where WHERE
                        where mode: display thread stacks of the given process
  -v, --verbose         verbose logging
  -V, --version         show program's version number and exit
```
The output is written to a file specified with the `--output` option. Curretly, this is in
the format of the normal [Austin][austin] format, that is collapsed stacks with
metadata at the top. This makes it easy to re-use existing visualisation tools,
like the [Austin VS Code][austin-vscode] extension.


## Compatibility

Supported platforms: Linux (amd64, i686), Darwin (amd64, aarch64)

Supported interpreters: CPython 3.8-3.13

### Notes

Attaching to a process (including in where mode) requires extra permissions. On
Unix, you can attach to a running process with `sudo`. On Linux, one may also
set the ptrace scope to `0` with `sudo sysctl kernel.yama.ptrace_scope=0` to
allow attaching to any process. However, this is not recommended for security
reasons.


## Where mode

The where mode is similar to [Austin][austin]'s where mode, that is Echion will
dump the stacks of all running threads to standard error. This is useful for
debugging deadlocks and other issues that may occur in a running process.

When running or attaching to a process, you can also send a `SIGQUIT` signal to
dump the stacks of all running threads. The result is similar to the where mode.
You can normally send a `SIGQUIT` signal with the <kbd>CTRL</kbd>+<kbd>\\</kbd>
key combination.


## Memory mode

Besides wall time and CPU time, Echion can be used to profile memory
allocations. In this mode, Echion tracks the Python memory domain allocators and
accounts for each single event. Because of the tracing nature, this mode
introduces considerable overhead, but gives pretty accurate results that can be
used to investigate potential memory leaks. To fully understand that data that
is collected in this mode, one should be aware of how Echion tracks allocations
and deallocations. When an allocation is made, Echion records the frame stack
that was involved and maps it to the returned memory address. When a
deallocation for a tracked memory address is made, the freed memory is accounted
for the same stack. Therefore, objects that are allocated and deallocated during
the tracking period account for a total of 0 allocated bytes. This means that
all the non-negative values reported by Echion represent memory that was still
allocated by the time the tracking ended.

*Since Echion 0.3.0*.


## Why Echion?

Sampling in-process comes with some benefits. One has easier access to more
information, like thread names, and potentially the task abstraction of async
frameworks, like `asyncio`, `gevent`, ... . Also available is more accurate
per-thread CPU timing information.

Currently, Echion supports sampling asyncio-based applications, but not in
native mode. This makes Echion the very first example of an async profiler for
CPython.

Echion relies on some assumptions to collect and sample all the running threads
without holding the GIL. This makes Echion very similar to tools like
[Austin][austin]. However, some features, like multiprocess support, are more
complicated to handle and would require the use of e.g. IPC solutions.
Furthermore, Echion normally requires that you install it within your
environment, wheareas [Austin][austin] can be installed indepdendently.


## How it works

On a fundamental level, there is one key assumption that Echion relies upon:

> The interpreter state object lives as long as the CPython process itself.

All unsafe memory reads are performed indirectly via copies of data structure
obtained with the use of system calls like `process_vm_readv`. This is
essentially what allows Echion to run its sampling thread without the GIL.

As for attaching to a running process, we make use of the [hypno][hypno] library
to inject Python code that bootstraps Echion into the target process.


[austin]: http://github.com/p403n1x87/austin
[austin-vscode]: https://marketplace.visualstudio.com/items?itemName=p403n1x87.austin-vscode
[hypno]: https://github.com/kmaork/hypno
