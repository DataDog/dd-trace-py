"""Check that the build host can load libclang and parse a C header."""

import ctypes
import ctypes.util
import glob
import os
from pathlib import Path
import tempfile


def main():
    configured = os.environ.get("LIBCLANG_PATH")
    if configured:
        candidates = glob.glob(str(Path(configured) / "libclang.*"))
    else:
        candidates = [ctypes.util.find_library("clang")]
        for pattern in ("/usr/lib/llvm-*/lib/libclang.so*", "/usr/lib/libclang.so*", "/usr/lib64/libclang.so*"):
            candidates.extend(glob.glob(pattern))
    library = None
    for candidate in candidates:
        if candidate:
            try:
                library = ctypes.CDLL(candidate)
                break
            except OSError:
                continue
    if library is None:
        raise RuntimeError(f"Cannot load libclang (LIBCLANG_PATH={configured!r})")

    arguments = [b"-x", b"c"]
    if os.environ.get("SDKROOT"):
        arguments.extend([b"-isysroot", os.fsencode(os.environ["SDKROOT"])])
    library.clang_createIndex.restype = ctypes.c_void_p
    library.clang_parseTranslationUnit.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    library.clang_parseTranslationUnit.restype = ctypes.c_void_p
    library.clang_getNumDiagnostics.argtypes = [ctypes.c_void_p]
    library.clang_getDiagnostic.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    library.clang_getDiagnostic.restype = ctypes.c_void_p
    library.clang_getDiagnosticSeverity.argtypes = [ctypes.c_void_p]
    library.clang_disposeDiagnostic.argtypes = [ctypes.c_void_p]
    library.clang_disposeTranslationUnit.argtypes = [ctypes.c_void_p]
    library.clang_disposeIndex.argtypes = [ctypes.c_void_p]
    index = library.clang_createIndex(0, 0)
    with tempfile.TemporaryDirectory() as directory:
        header = Path(directory) / "probe.h"
        header.write_text("typedef struct { int value; } ddtrace_libclang_probe;\n")
        args = (ctypes.c_char_p * len(arguments))(*arguments)
        unit = library.clang_parseTranslationUnit(index, os.fsencode(header), args, len(arguments), None, 0, 0)
        if not unit:
            raise RuntimeError("libclang could not parse a C header")
        try:
            for number in range(library.clang_getNumDiagnostics(unit)):
                diagnostic = library.clang_getDiagnostic(unit, number)
                severity = library.clang_getDiagnosticSeverity(diagnostic)
                library.clang_disposeDiagnostic(diagnostic)
                if severity >= 3:
                    raise RuntimeError("libclang reported an error parsing the C header")
        finally:
            library.clang_disposeTranslationUnit(unit)
            library.clang_disposeIndex(index)
    print(f"libclang C header parsing passed: {library._name}")


if __name__ == "__main__":
    main()
