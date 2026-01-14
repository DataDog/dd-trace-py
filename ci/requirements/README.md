# CI Dependencies Management

Locked CI dependencies ensure reproducible builds and prevent transitive dependency breakage.

## Files

- `ci.in` - Source dependencies (auto-generated from CI workflows)
- `ci.txt` - Locked requirements with hashes (auto-generated)

**Do not edit these files manually.** They are generated from `pip install` commands in CI workflows.

## Quick Start

### Update dependencies after changing CI workflows

```bash
scripts/update-ci-dependencies --compile --update-workflows
```

This scans CI workflows, extracts dependencies, generates locked requirements, and updates workflows.

### Upgrade to latest versions

```bash
uv pip compile --generate-hashes --upgrade ci/requirements/ci.in -o ci/requirements/ci.txt
```

## Platform-Specific Dependencies (Important!)

**On macOS:** Requirements must be compiled on Linux to include platform-specific dependencies (e.g., `keyring` needs `SecretStorage` on Linux but not macOS).

**Python Version:** Compile for Python 3.9 to ensure compatibility across all CI jobs (the project supports Python 3.9+).

Run this in Docker:

```bash
docker run --rm -v $(pwd):/work -w /work python:3.9 \
  bash -c 'pip install uv && uv pip compile --generate-hashes \
  --python-platform linux --python-version 3.9 ci/requirements/ci.in -o ci/requirements/ci.txt'
```

Or use uv locally with platform/version flags:

```bash
uv pip compile --generate-hashes --python-platform linux --python-version 3.9 ci/requirements/ci.in -o ci/requirements/ci.txt
```

## CI Validation

The CI pipeline checks that workflows use locked requirements. If it fails:

```bash
scripts/update-ci-dependencies --compile --update-workflows
git add ci/requirements/ .github/ .gitlab/
git commit -m "chore: update locked CI dependencies"
```

## Troubleshooting

**"CI validation failed" in CI but passes locally?**
- Platform-specific dependency missing (see section above)
- Recompile on Linux or with `--python-platform linux`

**Version conflicts?**
- Standardize package versions across workflows first
- Then run `scripts/update-ci-dependencies --compile --update-workflows`
