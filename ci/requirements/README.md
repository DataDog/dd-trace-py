# CI Dependencies Management

Locked CI dependencies ensure reproducible builds and prevent transitive dependency breakage.

## Files

- `ci.in` - Source dependencies (manually maintained)
- `ci.txt` - Locked requirements with hashes (auto-generated)

**Edit `ci.in` manually when adding new CI dependencies.** The `ci.txt` file is generated from `ci.in`.

## GitHub Actions vs GitLab CI

- **GitHub Actions** (`.github/workflows/`): Use locked requirements from `ci/requirements/ci.txt`
- **GitLab CI** (`.gitlab/`): Uses explicit version specifications (e.g., `pip install 'twine>=5.0,<7'`)
  - GitLab files are checked for version specifications but not auto-updated
  - This allows GitLab CI to have simpler, minimal dependencies without the full locked file

## Workflow

### Adding a new CI dependency

1. **Add to `ci.in`:**
   ```bash
   echo "new-package>=1.0,<2" >> ci/requirements/ci.in
   ```

2. **Regenerate `ci.txt`:**
   ```bash
   scripts/update-ci-dependencies
   ```

3. **Add to workflow:**
   ```yaml
   - name: Install CI dependencies
     run: pip install -r ci/requirements/ci.txt
   ```

4. **Verify:**
   ```bash
   scripts/check-ci-dependencies
   ```

### Upgrading dependencies

To upgrade all dependencies to their latest compatible versions:

```bash
uv pip compile --generate-hashes --upgrade \
  --python-platform linux --python-version 3.9 \
  ci/requirements/ci.in -o ci/requirements/ci.txt
```

Or just run the update script which uses the same flags:

```bash
scripts/update-ci-dependencies
```

## Platform-Specific Dependencies (Important!)

**On macOS:** Requirements must be compiled for Linux to include platform-specific dependencies (e.g., `keyring` needs `SecretStorage` on Linux but not macOS).

**Python Version:** Compile for Python 3.9 to ensure compatibility across all CI jobs (the project supports Python 3.9+).

The `scripts/update-ci-dependencies` script automatically uses the correct platform and Python version flags.

If you need to compile manually in Docker:

```bash
docker run --rm -v $(pwd):/work -w /work python:3.9 \
  bash -c 'pip install uv && uv pip compile --generate-hashes \
  --python-platform linux --python-version 3.9 ci/requirements/ci.in -o ci/requirements/ci.txt'
```

## CI Validation

The CI pipeline checks that:
1. All `pip install` commands in workflows use locked requirements or have explicit versions
2. All packages in workflows are present in `ci.in`

If validation fails:

```bash
# Check what's wrong
scripts/check-ci-dependencies

# Fix by updating ci.in and regenerating
vim ci/requirements/ci.in  # Add missing packages
scripts/update-ci-dependencies

# Commit
git add ci/requirements/
git commit -m "chore: update locked CI dependencies"
```

### Allowing Exceptions

In special cases where a job needs to install a package directly (e.g., a minimal test that only needs `twine`), you can mark it as allowed:

```yaml
# With version (recommended):
pip install 'twine>=5.0,<6'  # ci-deps: allow

# Without version (use sparingly):
pip install twine  # ci-deps: allow-no-version

# Or on the line before:
# ci-deps: allow
pip install 'some-package>=1.0'
```

**Note:** Using `# ci-deps: allow` requires a version specifier for safety. Use `# ci-deps: allow-no-version` only when necessary.

## Troubleshooting

**"CI validation failed" - Package not in ci.in?**
- Add the package to `ci/requirements/ci.in`
- Run `scripts/update-ci-dependencies`

**"CI validation failed" - No version specified?**
- Add version constraint: `pip install 'package>=1.0,<2'`
- Or add exception comment: `# ci-deps: allow`

**Platform-specific dependency missing?**
- Recompile with `scripts/update-ci-dependencies` (it uses Linux platform flags)
- Or manually with `--python-platform linux --python-version 3.9`

**Version conflicts?**
- Check `ci.in` for conflicting version constraints
- Adjust constraints to be compatible
- Run `scripts/update-ci-dependencies`
