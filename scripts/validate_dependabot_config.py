#!/usr/bin/env python3
"""Validate ``.github/dependabot.yml`` for rules GitHub only enforces late.

GitHub's *native* Dependabot config validator runs reliably against the merge
queue's merge commit but not against a PR's head commit, so config mistakes are
frequently only surfaced at merge time. This script reproduces the subset of
Dependabot's internal validation that is not encoded in the public JSON schema
(and is therefore missed by schema-only validators) so that the error is caught
on PR CI instead.

Currently enforced:

* ``cooldown.semver-major-days`` / ``semver-minor-days`` / ``semver-patch-days``
  are only supported for ecosystems that follow semantic versioning. Setting
  them for a non-semver ecosystem (``github-actions``, ``docker``,
  ``docker-compose``, ``terraform`` ...) makes Dependabot reject the whole
  config with::

      The property '#/updates/0/cooldown/semver-major-days' is not supported
      for the package ecosystem 'github-actions'.

Run from the repository root::

    python scripts/validate_dependabot_config.py

Exits with code 1 if any unsupported configuration is found.
"""

from __future__ import annotations

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / ".github" / "dependabot.yml"

# Ecosystems that do NOT follow semantic versioning. Dependabot rejects the
# ``semver-*-days`` cooldown keys for these. Keep in sync with the Dependabot
# docs if new non-semver ecosystems are adopted by this repo:
# https://docs.github.com/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file#cooldown
NON_SEMVER_ECOSYSTEMS = frozenset(
    {
        "github-actions",
        "docker",
        "docker-compose",
        "terraform",
        "gitsubmodule",
        "devcontainers",
    }
)

SEMVER_COOLDOWN_KEYS = (
    "semver-major-days",
    "semver-minor-days",
    "semver-patch-days",
)


def validate(config: dict) -> list[str]:
    errors: list[str] = []
    for index, update in enumerate(config.get("updates", []) or []):
        if not isinstance(update, dict):
            continue
        ecosystem = update.get("package-ecosystem")
        cooldown = update.get("cooldown")
        if not isinstance(cooldown, dict) or ecosystem not in NON_SEMVER_ECOSYSTEMS:
            continue
        for key in SEMVER_COOLDOWN_KEYS:
            if key in cooldown:
                errors.append(
                    f"updates[{index}] (package-ecosystem: '{ecosystem}'): "
                    f"cooldown.{key} is not supported for non-semver ecosystems. "
                    f"Use only 'default-days' for '{ecosystem}'."
                )
    return errors


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"No Dependabot config found at {CONFIG_PATH}; nothing to validate.")
        return 0

    with CONFIG_PATH.open() as fp:
        config = yaml.safe_load(fp)

    if not isinstance(config, dict):
        print(f"error: {CONFIG_PATH} did not parse to a mapping.", file=sys.stderr)
        return 1

    errors = validate(config)
    if errors:
        print(f"Invalid Dependabot configuration in {CONFIG_PATH.relative_to(ROOT)}:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"{CONFIG_PATH.relative_to(ROOT)} passed Dependabot cooldown validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
