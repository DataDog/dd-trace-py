#!/usr/bin/env python3
"""
Version ordering validation for release verification.

This module validates that a release version is the next logical semver version
considering all existing versions in the repository.

Key validation rules:
- Version must not already exist (duplicate check)
- Version must have a predecessor (no orphan releases)
- Version must fit logically in the sequence
- RC versions must come before final releases
- Patch versions in the same series must be sequential
- Backports are allowed (e.g., 4.1.3 after 4.2.0 exists)
"""

import sys
from packaging.version import Version, InvalidVersion


def validate_version(expected_str, existing_strs, git_history_most_recent_str=None):
    """
    Validate that expected_str is a valid next version for the release.

    Args:
        expected_str: Version string to validate (e.g., "4.1.0", "4.1.0rc1")
        existing_strs: List of all existing version strings globally (e.g., ["4.1.0", "4.0.0"])
        git_history_most_recent_str: Most recent tag in TARGET_SHA's commit history (for git history check)

    Returns:
        dict with keys:
            status: 'ok', 'duplicate', 'no_previous', 'invalid_sequence', or 'error'
            previous: (if status='ok') the previous version in sequence
            message: (if status != 'ok') error message

    Examples:
        >>> result = validate_version("4.1.1", ["4.1.0", "4.0.0"])
        >>> result['status']
        'ok'
        >>> str(result['previous'])
        '4.1.0'

        >>> result = validate_version("4.1.0", ["4.1.0", "4.0.0"])
        >>> result['status']
        'invalid_sequence'

        >>> result = validate_version("4.1.0rc2", ["4.1.0", "4.1.0rc1"])
        >>> result['status']
        'invalid_sequence'

        >>> result = validate_version("4.1.3", ["4.2.0", "4.1.2", "4.1.1", "4.1.0"])
        >>> result['status']
        'ok'
        >>> result.get('is_backport')
        True

        >>> result = validate_version("4.1.5", ["4.1.2"])
        >>> result['status']
        'invalid_sequence'

        >>> result = validate_version("5.0.0", ["4.9.0"])
        >>> result['status']
        'ok'

        >>> result = validate_version("1.0.0", [])
        >>> result['status']
        'invalid_sequence'

        >>> result = validate_version("invalid", ["1.0.0"])
        >>> result['status']
        'error'

        >>> result = validate_version("4.2.0", ["4.2.0rc1"])
        >>> result['status']
        'ok'

        >>> result = validate_version("4.0.2", ["4.2.0rc1", "4.1.2", "4.1.1", "4.1.0", "4.0.2"], git_history_most_recent_str="4.1.0rc1")
        >>> result['status']
        'invalid_sequence'
        >>> len(result.get('failures', []))
        3

        >>> result = validate_version("4.3.0", ["4.2.0rc1"], git_history_most_recent_str="4.2.0rc1")
        >>> result['status']
        'invalid_sequence'

        >>> result = validate_version("4.1.3", ["4.2.0rc1", "4.1.2", "4.1.1", "4.1.0"], git_history_most_recent_str="4.2.0rc1")
        >>> result['status']
        'invalid_sequence'

        >>> result = validate_version("4.2.0rc2", ["4.2.0rc1"], git_history_most_recent_str="4.2.0rc1")
        >>> result['status']
        'ok'

        >>> result = validate_version("4.1.3", ["4.2.0", "4.1.2", "4.1.1", "4.1.0"])
        >>> result['status']
        'ok'
    """
    try:
        expected = Version(expected_str)
    except InvalidVersion as e:
        return {
            'status': 'error',
            'message': f'Invalid expected version format: {expected_str}: {e}'
        }

    # Parse existing versions
    try:
        existing_versions = sorted([Version(v) for v in existing_strs])
    except InvalidVersion as e:
        return {
            'status': 'error',
            'message': f'Invalid version in existing versions: {e}'
        }

    # Collect all validation failures
    failures = []

    # Check 1: Git history validation (if provided)
    # Ensure the expected version > most recent tag in TARGET_SHA's commit history
    if git_history_most_recent_str:
        try:
            git_history_most_recent = Version(git_history_most_recent_str)
        except InvalidVersion as e:
            return {
                'status': 'error',
                'message': f'Invalid git history version: {git_history_most_recent_str}: {e}'
            }

        if expected <= git_history_most_recent:
            failures.append(
                f'Git history check failed: Version must be greater than the most recent tag in commit history. '
                f'Most recent: {git_history_most_recent}, Expected: {expected}'
            )

    # Check 2: Version doesn't already exist (global duplicate check)
    is_duplicate = expected in existing_versions
    if is_duplicate:
        failures.append(
            f'Duplicate check failed: Version {expected} already exists'
        )

    # Check 3: Must have a previous version
    if not existing_versions:
        failures.append(
            f'No previous versions found. Cannot release {expected} without a predecessor.'
        )

    # If we already have failures from early checks, still do global validation for comprehensive feedback
    # Check 4: Find where expected fits in the sequence
    previous = None
    next_ver = None

    if existing_versions:
        for ver in existing_versions:
            if ver < expected:
                previous = ver
            elif ver > expected:
                next_ver = ver
                if previous is not None:
                    # Found both previous and next
                    break

        # Check 5: Validate it's a logical next version (global check)
        # Ensures the version logically slots in (backports are allowed)
        # Important: previous is the highest version less than expected
        highest_overall = existing_versions[-1] if existing_versions else None

        validation = _is_valid_next_version(expected, previous, next_ver, highest_overall)
        if not validation['valid']:
            failures.append(
                f'Global check failed: {validation["message"]}'
            )

    # If we have any failures, return them all
    if failures:
        return {
            'status': 'invalid_sequence',
            'message': '\n'.join(failures),
            'failures': failures,
            'previous': previous,
            'next': next_ver,
        }

    # All checks passed
    result = {
        'status': 'ok',
        'previous': previous,
    }
    if previous and next_ver:
        if next_ver > expected or (existing_versions and Version(f"{existing_versions[-1].major}.{existing_versions[-1].minor}.{existing_versions[-1].micro}") > expected):
            # Check if this is a backport
            highest_base = Version(f"{existing_versions[-1].major}.{existing_versions[-1].minor}.{existing_versions[-1].micro}")
            expected_base = Version(f"{expected.major}.{expected.minor}.{expected.micro}")
            if expected_base < highest_base:
                result['is_backport'] = True

    return result


def _is_valid_next_version(expected, previous, next_ver, highest_overall=None):
    """
    Validate that expected is a valid next version after previous (and before next_ver if it exists).

    Args:
        expected: The version we want to release
        previous: The highest version less than expected
        next_ver: The lowest version greater than expected (if any)
        highest_overall: The absolute highest version in the repo

    Returns:
        dict with 'valid': bool and 'message': str (if not valid), 'is_backport': bool (if valid)
    """
    if previous is None:
        return {'valid': False, 'message': 'No previous version found'}

    # Check if it's an RC version
    expected_is_rc = expected.is_prerelease
    previous_is_rc = previous.is_prerelease
    next_is_rc = next_ver.is_prerelease if next_ver else False

    # Extract base versions (without pre-release info)
    expected_base = Version(f"{expected.major}.{expected.minor}.{expected.micro}")
    previous_base = Version(f"{previous.major}.{previous.minor}.{previous.micro}")
    next_base = Version(f"{next_ver.major}.{next_ver.minor}.{next_ver.micro}") if next_ver else None

    # Get highest overall base version (without prerelease info)
    highest_base = None
    if highest_overall:
        highest_base = Version(f"{highest_overall.major}.{highest_overall.minor}.{highest_overall.micro}")

    # Rule 1: Cannot skip to a newer major.minor series if there's an outstanding RC
    # E.g., if 4.2.0rc1 exists, cannot release 4.3.0 - must release 4.2.0 first
    # (But backporting to older series is OK)
    if highest_overall and highest_overall.is_prerelease:
        highest_base = Version(f"{highest_overall.major}.{highest_overall.minor}.{highest_overall.micro}")
        # Check if expected is newer than the RC's base version (i.e., different series)
        if expected_base > highest_base:
            return {
                'valid': False,
                'message': f'Cannot release {expected} while RC version {highest_overall} is outstanding. '
                          f'Must release {highest_base} (final) before moving to a newer series.'
            }

    # Rule 2: Cannot release RC if final version already exists
    # E.g., 4.1.0rc2 after 4.1.0 is released is invalid
    if expected_is_rc and next_ver and not next_is_rc:
        # Expected is RC, next exists and is final
        if expected_base == next_base:
            return {
                'valid': False,
                'message': f'Cannot release {expected} because the final version {next_ver} already exists. '
                           'RC versions must come before the final release.'
            }

    # Check if this is a backport (releasing to older series when newer exists)
    is_backport = False
    if next_ver and next_base and not expected_is_rc and not next_is_rc:
        # Both expected and next are final versions
        if expected_base.major < next_base.major or (
            expected_base.major == next_base.major and expected_base.minor < next_base.minor
        ):
            # Expected is from an older series than next
            is_backport = True

    # Rule 2: For patch versions in the same major.minor series, must be sequential
    # E.g., 4.1.2 after 4.1.0 is invalid (should be 4.1.1 first)
    # But 4.1.3 after 4.2.0 is valid (backport)
    if not expected_is_rc and not previous_is_rc:
        # Both are final versions
        if expected_base.major == previous_base.major and expected_base.minor == previous_base.minor:
            # Same major.minor series - must be sequential patch
            if expected_base.micro != previous_base.micro + 1:
                # But allow if we're backporting (next version is from a newer series)
                if not is_backport:
                    return {
                        'valid': False,
                        'message': f'Patch version must be sequential in {expected_base.major}.{expected_base.minor} series. '
                                  f'After {previous}, expected {previous_base.major}.{previous_base.minor}.{previous_base.micro + 1}, '
                                  f'but got {expected}'
                    }

    # Rule 3: RC sequence must be logical
    # E.g., 4.1.0rc2 after 4.1.0rc1 is valid
    # E.g., 4.1.0rc3 after 4.1.0rc1 is invalid (must be sequential)
    if expected_is_rc and previous_is_rc:
        if expected_base == previous_base:
            # Same final version, must be sequential RC numbers
            expected_rc_num = int(expected.pre[1])
            previous_rc_num = int(previous.pre[1])
            if expected_rc_num != previous_rc_num + 1:
                return {
                    'valid': False,
                    'message': f'RC versions must be sequential. '
                              f'After rc{previous_rc_num}, expected rc{previous_rc_num + 1}, '
                              f'but got rc{expected_rc_num}'
                }

    # All checks passed
    result = {'valid': True}
    if is_backport:
        result['is_backport'] = True
    return result


def main():
    """CLI interface for version validation."""
    if len(sys.argv) < 2:
        print("Usage: verify-version-ordering.py <expected_version> [--git-history-most-recent <version>] [existing_versions...]", file=sys.stderr)
        sys.exit(2)

    expected_version = sys.argv[1]
    git_history_most_recent = None
    existing_versions = []

    # Parse arguments
    i = 2
    if i < len(sys.argv) and sys.argv[i] == "--git-history-most-recent":
        if i + 1 < len(sys.argv):
            git_history_most_recent = sys.argv[i + 1]
            i += 2

    existing_versions = sys.argv[i:] if i < len(sys.argv) else []

    result = validate_version(expected_version, existing_versions, git_history_most_recent)

    if result['status'] == 'ok':
        previous = result.get('previous', 'unknown')
        is_backport = result.get('is_backport', False)
        if is_backport:
            print(f"✅ Version {expected_version} is valid (backport release after {previous})")
        else:
            print(f"✅ Version {expected_version} is valid (next version after {previous})")
        sys.exit(0)
    else:
        # Print all failures
        message = result.get('message', 'Unknown error')
        print(f"❌ FAILED: Multiple validation errors:", file=sys.stderr)
        print(file=sys.stderr)
        for line in message.split('\n'):
            print(f"  • {line}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
