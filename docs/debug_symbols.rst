Debugging Native Extensions with Debug Symbols
==============================================

dd-trace-py is built with debug symbols by default, and packaged separately from the main wheel files to reduce the size of the primary distribution packages.

Debug Symbol Files
------------------

The project generates debug symbols during the build process:

- **Linux**: ``.debug`` files (using ``objcopy --only-keep-debug``)
- **macOS**: ``.dSYM`` bundles (using ``dsymutil``)

These debug symbols are extracted from the main wheels and packaged into separate `.zip` files with the naming convention:

::

    {original-wheel-name}-debug-symbols.zip

For example:

- ``ddtrace-1.20.0-cp39-cp39-linux_x86_64.whl`` → ``ddtrace-1.20.0-cp39-cp39-linux_x86_64-debug-symbols.zip``
- ``ddtrace-1.20.0-cp39-cp39-macosx_10_9_x86_64.whl`` → ``ddtrace-1.20.0-cp39-cp39-macosx_10_9_x86_64-debug-symbols.zip``

Build Process
-------------

The debug symbols are handled automatically during the CI build process:

1. Wheels are built with debug symbols included
2. Debug symbols are extracted using the ``scripts/extract_debug_symbols.py`` script
3. Debug symbols are removed from the main wheel to reduce size
4. Separate debug symbol packages are created and uploaded as artifacts

Usage
-----

To use debug symbols for debugging or crash analysis:

1. Download the appropriate debug symbol package for your platform and Python version
2. Extract the debug symbol files to the same directory as the corresponding `.so` files.
   Typically, the site-packages directory where ddtrace is installed.
3. Your debugger or crash analysis tool should automatically find the debug symbols
4. To view assembly with code side by side, you also need the source code, and
   set substitute paths in your debugger to the source code directory. For example,
   for ``_stack_v2.cpython-313-x86_64-linux-gnu.so`` is compiled from
   echion as specified in ``ddtrace/internal/datadog/profiling/stack_v2/CMakeLists.txt``.
   So you first need to check out the echion repository and checkout the commit hash.
   Then, set substitute paths in gdb to the echion source code directory.
   Typically, if you run ``dias /m <symbol>`` in gdb, it will tell you the full
   file path of the source code as the following:

   .. code-block:: bash

       (gdb) disas /m Frame::read
       Dump of assembler code for function _ZN5Frame4readEP19_PyInterpreterFramePS1_:
       269     /project/build/cmake.linux-x86_64-cpython-313/ddtrace.internal.datadog.profiling.stack_v2._stack_v2/_deps/echion-src/echion/frame.cc: No such file or directory.
          0x000000000000ece4 <+0>:     push   %r12
          0x000000000000ece6 <+2>:     mov    %rdi,%r8
          0x000000000000ece9 <+5>:     push   %rbp
          0x000000000000ecea <+6>:     mov    %rsi,%rbp
          0x000000000000eced <+9>:     push   %rbx
          0x000000000000ecee <+10>:    sub    $0x60,%rsp

       270     in /project/build/cmake.linux-x86_64-cpython-313/ddtrace.internal.datadog.profiling.stack_v2._stack_v2/_deps/echion-src/echion/frame.cc
       271     in /project/build/cmake.linux-x86_64-cpython-313/ddtrace.internal.datadog.profiling.stack_v2._stack_v2/_deps/echion-src/echion/frame.cc

   Then you can set substitute paths in gdb to the echion source code directory

   .. code-block:: bash

       (gdb) set substitute-path /project/build/cmake.linux-x86_64-cpython-313/ddtrace.internal.datadog.profiling.stack_v2._stack_v2/_deps/echion-src/echion /path/to/echion/source/code

   Run ``dias /m Frame::read`` again to see the assembly with code side by side.

   .. code-block:: bash

       (gdb) disas /m Frame::read
       Dump of assembler code for function _ZN5Frame4readEP19_PyInterpreterFramePS1_:
       warning: Source file is more recent than executable.
       269     {
          0x000000000000ece4 <+0>:     push   %r12
          0x000000000000ece6 <+2>:     mov    %rdi,%r8
          0x000000000000ece9 <+5>:     push   %rbp
          0x000000000000ecea <+6>:     mov    %rsi,%rbp
          0x000000000000eced <+9>:     push   %rbx
          0x000000000000ecee <+10>:    sub    $0x60,%rsp

       270     #if PY_VERSION_HEX >= 0x030b0000
       271         _PyInterpreterFrame iframe;

       272     #if PY_VERSION_HEX >= 0x030d0000
       273         // From Python versions 3.13, f_executable can have objects other than
       274         // code objects for an internal frame. We need to skip some frames if
       275         // its f_executable is not code as suggested here:
       276         // https://github.com/python/cpython/issues/100987#issuecomment-1485556487
       277         PyObject f_executable;

       278
       279         for (; frame_addr; frame_addr = frame_addr->previous)
          0x000000000000ecf7 <+19>:    test   %r8,%r8
          0x000000000000ecfa <+22>:    je     0xed91 <_ZN5Frame4readEP19_PyInterpreterFramePS1_+173>
          0x000000000000ed88 <+164>:   mov    0x8(%rbx),%r8
          0x000000000000ed8c <+168>:   jmp    0xecf7 <_ZN5Frame4readEP19_PyInterpreterFramePS1_+19>

      On lldb, you can find the source code full path by running ``image lookup -n Frame::read --verbose``,
      and set the source code path using ``settings set target.source-map <expected-path> <actual-path>``.
