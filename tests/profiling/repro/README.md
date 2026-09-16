# SIGSEGV handler takeover repro (PROF-14568 / IR-60700)

Manual harness for foreign SIGSEGV/SIGBUS handler takeover. One scenario per process.

## Build

```bash
gcc -shared -fPIC -O0 -o libforeign.so foreign_handler.c
```

`libforeign.so` is a build artifact; do not commit it.

## Run

```bash
export REPRO_FOREIGN_LIB=$PWD/libforeign.so
export DD_PROFILING_ENABLED=1 _DD_PROFILING_STACK_FAST_COPY=1

# Native sigaction handler chained onto ddtrace's handler (production-like path)
REPRO_SCENARIO=foreign-native python segv_handler_repro.py
```

Other scenarios: `control`, `owned`, `foreign`, `faulthandler`. See the script docstring.
