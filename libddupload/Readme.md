This is a very rough sketch for how to use libdatadog

1. Download the v2 release of libdatadog from https://github.com/DataDog/libdatadog/releases/tag/v2.0.0
2. untar that release to this directory.  The harness assumes you're using the libc-x86 version.  You'll have to change the toplevel pyrun.sh and build.sh accordingly if you downloaded something else.  Obviously this won't be a feature of the finished version of this...
3. Execute any Python scripts through the toplevel pyrun.sh, like `./pyrun.sh tests/profiling/collatz.py
4. Refer to tests/profiling/collatz.py for examples on how to switch profiling modes
