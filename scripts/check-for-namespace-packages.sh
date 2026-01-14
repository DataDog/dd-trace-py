#!/usr/bin/env bash
set -euo pipefail

# TODO: Can we also check tests/? some folders in there require missing __init__.py
ROOTS=("ddtrace")
FAIL=0

echo "Checking for accidental namespace packages..."

for ROOT in "${ROOTS[@]}"; do
    echo "Scanning '$ROOT'..."

    # Only check roots that actually exist (CI safety)
    if [[ ! -d "$ROOT" ]]; then
        echo "  (skipped: directory does not exist)"
        continue
    fi

    # Walk all directories under the root
    while IFS= read -r dir; do
        # Skip __pycache__
        [[ "$dir" == *"__pycache__"* ]] && continue

        # Directory contains Python or Cython files?
        if compgen -G "$dir/*.py" >/dev/null || compgen -G "$dir/*.pyx" >/dev/null; then

            # And it must contain __init__.py
            if [[ ! -f "$dir/__init__.py" ]]; then
                echo "❌ Missing __init__.py in: $dir"
                FAIL=1
            fi
        fi

    done < <(find "$ROOT" -type d)
done

if [[ "$FAIL" -eq 1 ]]; then
    echo
    echo "❌ ERROR: Missing __init__.py detected in source or test packages."
    echo "   Please add empty __init__.py files to the directories listed above."
    exit 1
else
    echo "✅ All Python package directories contain __init__.py"
fi
