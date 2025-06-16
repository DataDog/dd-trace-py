#!/usr/bin/env python3
"""
Generate component-specific hashes for build caching.

This script creates separate hashes for different build components:
- CMake extensions (IAST, DDUP, crashtracker, stack_v2)
- Rust extensions
- Cython extensions
- Regular C/C++ extensions
- Python source files that affect builds

Each component can be cached independently, allowing for partial rebuilds
when only some components have changed.
"""

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set


def get_file_hash(filepath: Path) -> str:
    """Get SHA256 hash of a file."""
    try:
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except (OSError, IOError):
        return ""


def get_files_hash(file_patterns: List[str], base_path: Path = None) -> str:
    """Get combined hash of multiple files/patterns."""
    if base_path is None:
        base_path = Path.cwd()
    
    files = []
    for pattern in file_patterns:
        if '*' in pattern or '?' in pattern:
            # Use glob pattern
            files.extend(base_path.glob(pattern))
        else:
            # Direct file path
            file_path = base_path / pattern
            if file_path.exists():
                files.append(file_path)
    
    # Sort files for consistent hashing
    files = sorted(files)
    
    hasher = hashlib.sha256()
    for file_path in files:
        if file_path.is_file():
            hasher.update(str(file_path.relative_to(base_path)).encode())
            hasher.update(get_file_hash(file_path).encode())
    
    return hasher.hexdigest()


def get_pyenv_versions_hash() -> str:
    """Get hash of installed Python versions."""
    try:
        result = subprocess.run(['pyenv', 'versions'], 
                              capture_output=True, text=True, check=True)
        versions = sorted(result.stdout.strip().split('\n'))
        return hashlib.sha256('\n'.join(versions).encode()).hexdigest()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return hashlib.sha256(f"python-{sys.version}".encode()).hexdigest()


def generate_component_hashes(base_path: Path = None) -> Dict[str, str]:
    """Generate hashes for each build component."""
    if base_path is None:
        base_path = Path.cwd()
    
    # Common files that affect all builds
    common_files = [
        'setup.py',
        'pyproject.toml',
        'hatch.toml',
    ]
    
    # Component definitions with their associated files
    components = {
        'cmake_iast': [
            'ddtrace/appsec/_iast/_taint_tracking/**/*.c',
            'ddtrace/appsec/_iast/_taint_tracking/**/*.cpp',
            'ddtrace/appsec/_iast/_taint_tracking/**/*.h',
            'ddtrace/appsec/_iast/_taint_tracking/**/CMakeLists.txt',
        ],
        'cmake_ddup': [
            'ddtrace/internal/datadog/profiling/ddup/**/*.c',
            'ddtrace/internal/datadog/profiling/ddup/**/*.cpp',
            'ddtrace/internal/datadog/profiling/ddup/**/*.h',
            'ddtrace/internal/datadog/profiling/ddup/**/CMakeLists.txt',
            'ddtrace/internal/datadog/profiling/ddup/**/*.rs',
            'ddtrace/internal/datadog/profiling/ddup/**/Cargo.*',
        ],
        'cmake_crashtracker': [
            'ddtrace/internal/datadog/profiling/crashtracker/**/*.c',
            'ddtrace/internal/datadog/profiling/crashtracker/**/*.cpp',
            'ddtrace/internal/datadog/profiling/crashtracker/**/*.h',
            'ddtrace/internal/datadog/profiling/crashtracker/**/CMakeLists.txt',
            'ddtrace/internal/datadog/profiling/crashtracker/**/*.rs',
            'ddtrace/internal/datadog/profiling/crashtracker/**/Cargo.*',
        ],
        'cmake_stack_v2': [
            'ddtrace/internal/datadog/profiling/stack_v2/**/*.c',
            'ddtrace/internal/datadog/profiling/stack_v2/**/*.cpp',
            'ddtrace/internal/datadog/profiling/stack_v2/**/*.h',
            'ddtrace/internal/datadog/profiling/stack_v2/**/CMakeLists.txt',
            'ddtrace/internal/datadog/profiling/stack_v2/**/*.rs',
            'ddtrace/internal/datadog/profiling/stack_v2/**/Cargo.*',
        ],
        'rust_native': [
            'src/native/**/*.rs',
            'src/native/**/Cargo.*',
        ],
        'cython_extensions': [
            'ddtrace/internal/_rand.pyx',
            'ddtrace/internal/_tagset.pyx',
            'ddtrace/internal/_encoding.pyx',
            'ddtrace/internal/telemetry/metrics_namespaces.pyx',
            'ddtrace/profiling/collector/stack.pyx',
            'ddtrace/profiling/collector/_traceback.pyx',
            'ddtrace/profiling/_threading.pyx',
            'ddtrace/profiling/collector/_task.pyx',
        ],
        'c_extensions': [
            'ddtrace/profiling/collector/_memalloc.c',
            'ddtrace/profiling/collector/_memalloc_tb.c',
            'ddtrace/profiling/collector/_memalloc_heap.c',
            'ddtrace/profiling/collector/_memalloc_reentrant.c',
            'ddtrace/profiling/collector/_memalloc_heap_map.c',
            'ddtrace/internal/_threads.cpp',
            'ddtrace/appsec/_iast/_stacktrace.c',
            'ddtrace/appsec/_iast/_ast/iastpatch.c',
        ],
        'vendor_extensions': [
            'ddtrace/vendor/psutil/**/*.c',
            'ddtrace/vendor/psutil/**/*.h',
            'ddtrace/vendor/psutil/setup.py',
            'ddtrace/vendor/**/setup.py',
        ],
    }
    
    hashes = {}
    
    # Get pyenv versions hash (affects all components)
    pyenv_hash = get_pyenv_versions_hash()
    
    # Get common files hash (affects all components)
    common_hash = get_files_hash(common_files, base_path)
    
    # Generate hash for each component
    for component_name, file_patterns in components.items():
        # Combine component-specific files with common files and pyenv
        component_files = []
        for pattern in file_patterns:
            if '**' in pattern:
                # Recursive glob
                component_files.extend(base_path.rglob(pattern.replace('**/', '')))
            elif '*' in pattern:
                # Simple glob
                component_files.extend(base_path.glob(pattern))
            else:
                # Direct file
                file_path = base_path / pattern
                if file_path.exists():
                    component_files.append(file_path)
        
        # Calculate hash for this component
        component_hasher = hashlib.sha256()
        component_hasher.update(common_hash.encode())
        component_hasher.update(pyenv_hash.encode())
        
        # Sort files for consistent hashing
        component_files = sorted(set(component_files))
        for file_path in component_files:
            if file_path.is_file():
                component_hasher.update(str(file_path.relative_to(base_path)).encode())
                component_hasher.update(get_file_hash(file_path).encode())
        
        hashes[component_name] = component_hasher.hexdigest()[:16]  # Use first 16 chars
    
    # Also generate the combined hash for backward compatibility
    all_hasher = hashlib.sha256()
    for component_hash in sorted(hashes.values()):
        all_hasher.update(component_hash.encode())
    hashes['combined'] = all_hasher.hexdigest()[:16]
    
    return hashes


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate component-specific build hashes')
    parser.add_argument('--format', choices=['json', 'env', 'shell'], default='env',
                       help='Output format (default: env)')
    parser.add_argument('--component', help='Get hash for specific component only')
    parser.add_argument('--base-path', type=Path, help='Base path for file resolution')
    
    args = parser.parse_args()
    
    hashes = generate_component_hashes(args.base_path)
    
    if args.component:
        if args.component in hashes:
            print(hashes[args.component])
        else:
            print(f"Unknown component: {args.component}", file=sys.stderr)
            print(f"Available components: {', '.join(sorted(hashes.keys()))}", file=sys.stderr)
            sys.exit(1)
    elif args.format == 'json':
        print(json.dumps(hashes, indent=2))
    elif args.format == 'shell':
        for component, hash_value in sorted(hashes.items()):
            print(f"export DD_{component.upper()}_HASH={hash_value}")
    else:  # env format
        for component, hash_value in sorted(hashes.items()):
            print(f"DD_{component.upper()}_HASH={hash_value}")


if __name__ == '__main__':
    main() 