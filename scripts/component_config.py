#!/usr/bin/env python3
"""
Centralized component configuration for ddtrace build system.

This module defines all component metadata in one place to avoid duplication
across setup.py, build_cache.py, get_component_hashes.py, and test files.
"""

from typing import List
from typing import Set


class ComponentConfig:
    """Centralized configuration for build components."""

    # Component definitions with their source paths for hashing
    COMPONENT_PATHS = {
        "cmake_iast": [
            "ddtrace/appsec/_iast/_taint_tracking/",
            "ddtrace/appsec/_iast/_taint_tracking/CMakeLists.txt",
        ],
        "cmake_ddup": [
            "ddtrace/internal/datadog/profiling/ddup/",
            "ddtrace/internal/datadog/profiling/ddup/CMakeLists.txt",
            "ddtrace/internal/datadog/profiling/include/",
            "ddtrace/internal/datadog/profiling/src/",
        ],
        "cmake_crashtracker": [
            "ddtrace/internal/datadog/profiling/crashtracker/",
            "ddtrace/internal/datadog/profiling/crashtracker/CMakeLists.txt",
            "ddtrace/internal/datadog/profiling/include/",
            "ddtrace/internal/datadog/profiling/src/",
        ],
        "cmake_stack_v2": [
            "ddtrace/internal/datadog/profiling/stack_v2/",
            "ddtrace/internal/datadog/profiling/stack_v2/CMakeLists.txt",
            "ddtrace/internal/datadog/profiling/include/",
            "ddtrace/internal/datadog/profiling/src/",
        ],
        "rust_native": [
            "src/native/",
            "src/native/Cargo.toml",
        ],
        "cython_extensions": [
            "ddtrace/internal/_rand.pyx",
            "ddtrace/internal/_tagset.pyx",
            "ddtrace/internal/_encoding.pyx",
            "ddtrace/internal/telemetry/metrics_namespaces.pyx",
            "ddtrace/profiling/collector/stack.pyx",
            "ddtrace/profiling/collector/_traceback.pyx",
            "ddtrace/profiling/_threading.pyx",
            "ddtrace/profiling/collector/_task.pyx",
        ],
        "c_extensions": [
            "ddtrace/profiling/collector/_memalloc.c",
            "ddtrace/profiling/collector/_memalloc_tb.c",
            "ddtrace/profiling/collector/_memalloc_heap.c",
            "ddtrace/profiling/collector/_memalloc_reentrant.c",
            "ddtrace/profiling/collector/_memalloc_heap_map.c",
            "ddtrace/internal/_threads.cpp",
            "ddtrace/appsec/_iast/_stacktrace.c",
            "ddtrace/appsec/_iast/_ast/iastpatch.c",
        ],
        "vendor_extensions": [
            "ddtrace/vendor/",
        ],
    }

    # Extension to component mapping
    EXTENSION_TO_COMPONENT = {
        "ddtrace.appsec._iast._taint_tracking._native": "cmake_iast",
        "ddtrace.internal.datadog.profiling.ddup._ddup": "cmake_ddup",
        "ddtrace.internal.datadog.profiling.crashtracker._crashtracker": "cmake_crashtracker",
        "ddtrace.internal.datadog.profiling.stack_v2._stack_v2": "cmake_stack_v2",
        "ddtrace.internal.native._native": "rust_native",
        "ddtrace.internal._rand": "cython_extensions",
        "ddtrace.internal._tagset": "cython_extensions",
        "ddtrace.internal._encoding": "cython_extensions",
        "ddtrace.internal.telemetry.metrics_namespaces": "cython_extensions",
        "ddtrace.profiling.collector.stack": "cython_extensions",
        "ddtrace.profiling.collector._traceback": "cython_extensions",
        "ddtrace.profiling._threading": "cython_extensions",
        "ddtrace.profiling.collector._task": "cython_extensions",
        "ddtrace.profiling.collector._memalloc": "c_extensions",
        "ddtrace.internal._threads": "c_extensions",
        "ddtrace.appsec._iast._stacktrace": "c_extensions",
        "ddtrace.appsec._iast._ast.iastpatch": "c_extensions",
    }

    # Component types for different cache behaviors
    CMAKE_COMPONENTS = {"cmake_iast", "cmake_ddup", "cmake_crashtracker", "cmake_stack_v2"}
    RUST_COMPONENTS = {"rust_native"}
    PYTHON_COMPONENTS = {"cython_extensions", "c_extensions", "vendor_extensions"}

    @classmethod
    def get_all_components(cls) -> List[str]:
        """Get list of all component names."""
        return list(cls.COMPONENT_PATHS.keys())

    @classmethod
    def get_component_paths(cls, component: str) -> List[str]:
        """Get source paths for a component."""
        return cls.COMPONENT_PATHS.get(component, [])

    @classmethod
    def get_extension_component(cls, extension_name: str) -> str:
        """Get component name for an extension."""
        return cls.EXTENSION_TO_COMPONENT.get(extension_name)

    @classmethod
    def get_component_cache_dir_name(cls, component: str) -> str:
        """Get cache directory name for a component."""
        # Map component names to cache directory names
        cache_dir_mapping = {
            "cmake_iast": "cmake_iast",
            "cmake_ddup": "cmake_ddup",
            "cmake_crashtracker": "cmake_crashtracker",
            "cmake_stack_v2": "cmake_stack_v2",
            "rust_native": "rust_native",
            "cython_extensions": "cython",
            "c_extensions": "c_extensions",
            "vendor_extensions": "vendor",
        }
        return cache_dir_mapping.get(component, component)

    @classmethod
    def is_cmake_component(cls, component: str) -> bool:
        """Check if component is a CMake component."""
        return component in cls.CMAKE_COMPONENTS

    @classmethod
    def is_rust_component(cls, component: str) -> bool:
        """Check if component is a Rust component."""
        return component in cls.RUST_COMPONENTS

    @classmethod
    def is_python_component(cls, component: str) -> bool:
        """Check if component is a Python component."""
        return component in cls.PYTHON_COMPONENTS

    @classmethod
    def get_components_by_type(cls, component_type: str) -> Set[str]:
        """Get components by type (cmake, rust, python)."""
        if component_type == "cmake":
            return cls.CMAKE_COMPONENTS
        elif component_type == "rust":
            return cls.RUST_COMPONENTS
        elif component_type == "python":
            return cls.PYTHON_COMPONENTS
        else:
            return set()
