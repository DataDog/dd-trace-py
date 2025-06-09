import ast
import re
from pathlib import Path
from typing import Dict
from typing import Set

from ddtrace.vendor.packaging.specifiers import InvalidSpecifier
from ddtrace.vendor.packaging.specifiers import Specifier


def _get_major_minor(version_str: str) -> tuple[int, int]:
    """Extract major.minor as integers from a version string."""
    try:
        parts = version_str.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return (0, 0)


def _get_integration_supported_versions(internal_contrib_dir: Path, integration_name: str) -> Dict[str, str]:
    """Extract _supported_versions from an integration's patch.py file using text parsing. We
    use regex to find the supported versions instead of importing due to not having the patched
    module installed within the test environment."""

    patch_file = internal_contrib_dir / integration_name / "patch.py"
    if not patch_file.exists():
        return {}
    
    try:
        with open(patch_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Look for the _supported_versions function and its return statement
        # Pattern matches: def _supported_versions(...): ... return {...}
        pattern = r'def _supported_versions\([^)]*\).*?return\s+(\{[^}]*\})'
        match = re.search(pattern, content, re.DOTALL)
        
        if match:
            return_dict_str = match.group(1)
            try:
                # Try to safely evaluate the dictionary literal
                supported_versions = ast.literal_eval(return_dict_str)
                print(f"✅ Supported versions: {supported_versions}")
                return supported_versions
            except (ValueError, SyntaxError):
                # If it's not a simple literal, try a simpler string-based approach
                # Look for patterns like {"module": ">=1.0", "other": "*"}
                simple_pattern = r'"([^"]+)":\s*"([^"]*)"'
                matches = re.findall(simple_pattern, return_dict_str)
                if matches:
                    return dict(matches)
        
    except Exception as e:
        print(f"❌ Failed to parse {integration_name}: {e}")
    
    print(f"❌ Failed to parse {integration_name}")
    return {}


def test_supported_versions_align_with_registry(
    internal_contrib_dir: Path, 
    registry_data: list[dict],
    integration_dir_names: Set[str]
):
    """Test that minimum tested versions correspond to supported version constraints."""
    errors = []
    
    registry_by_name = {entry["integration_name"]: entry for entry in registry_data}
    
    for integration_name in integration_dir_names:
        print(f"Testing integration: {integration_name}")
        supported_versions = _get_integration_supported_versions(internal_contrib_dir, integration_name)
        
        if not supported_versions:
            print(f"❌ No supported versions found for {integration_name}")
            continue
        
        registry_entry = registry_by_name.get(integration_name)
        if not registry_entry or not registry_entry.get("is_external_package", False) or not registry_entry.get("is_tested", True):
            continue
        
        tested_versions = registry_entry.get("tested_versions_by_dependency", {})
        
        for module_name, version_constraint in supported_versions.items():
            if version_constraint == "*":
                continue
                
            try:
                specifier = Specifier(version_constraint)
            except InvalidSpecifier:
                print(f"❌ Invalid specifier: {version_constraint}")
                continue
                
            # Check if any dependency in registry has min tested version matching the constraint
            found_matching_dependency = False
            for _, tested_range in tested_versions.items():
                min_tested = tested_range.get("min")
                if not min_tested:
                    continue
                    
                constraint_major_minor = _get_major_minor(specifier.version)
                tested_major_minor = _get_major_minor(min_tested)
                
                if tested_major_minor == constraint_major_minor:
                    print(f"✅ Found matching dependency: {module_name} {min_tested} {version_constraint}")
                    found_matching_dependency = True
                    break
            
            if not found_matching_dependency:
                print(f"❌ No matching dependency found for {module_name} {version_constraint}")
                constraint_major_minor = _get_major_minor(specifier.version)
                expected_major_minor = f"{constraint_major_minor[0]}.{constraint_major_minor[1]}"
                
                errors.append(
                    f"Integration '{integration_name}', Module '{module_name}': "
                    f"No dependency found with minimum tested version matching supported constraint '{version_constraint}' "
                    f"(expected major.minor: {expected_major_minor}). Minimum tested version: {min_tested}"
                )
    
    assert not errors, "\n".join(errors)
