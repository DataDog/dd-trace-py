# What this is about

We want to dogfood the latest version of `dd-trace-py` in our internal repositories
dd-source and dogweb.

There are helpers to make that possible, but you will need to coordinate the various steps.

You are free to use the following worktrees:
- `dd-trace-py`: `/home/bits/go/src/github.com/DataDog/ddtpy-dogfooding`
- `dogweb`: `/home/bits/go/src/github.com/DataDog/dogweb-dogfooding`
- `dd-source`: `/home/bits/go/src/github.com/DataDog/dd-source-dogfooding`

Note the default branches differ per repo:

| Repo | Default branch |
|------|----------------|
| `dd-trace-py` | `main` |
| `dd-source` | `main` |
| `dogweb` | `prod` |

# Pre-flight checks

Before you start working, please check that everything you will possibly need is available.
- Make sure the worktrees I listed exist and match my description
- Make sure the `dd-trace-py` worktree is clean (no unstaged changes, no new files)
- Make sure the `dogweb` worktree is clean (no unstaged changes, no new files)
- Make sure the `dd-source` workree is clean (no unstaged changes, no new files)
- Check that the `rapid` CLI is available. If not, propose to install it using
  `brew install rapid` on macOS and `update-tool rapid` in workspaces.
- Check that `bzl` is on the latest version by upgrading it using `dogbrew install bzl`.
- Check that you can read the ADMS Python prerelease index (see "Finding the dogfooding build"
  below) — this is how you discover the version to pin.
- Make sure Vault (Google OIDC) authentication is ready *now*, while the user is still watching,
  so `bzl run //:requirements.update` in dd-source doesn't block on a login prompt later:
  1. Check that the `token_helper` configured in `~/.vault` points to an existing executable. If
     it doesn't, `bzl` fails with `Error initializing token helper`; tell the user and suggest
     `ddtool vault install-token-helper`.
  2. Run `ddtool auth login --mode device --datacenter us1.ddbuild.io` in the background and
     watch its output. If the session is still valid, it just prints
     `session for "https://vault.us1.ddbuild.io" valid until <time>` and exits. If that time is
     less than 2 hours away, re-run it with `--force` to start a fresh session.
     Otherwise it prints a `https://www.google.com/device` link and a code: **relay the code to
     the user immediately** (it expires in minutes) and wait for the command to exit
     successfully before continuing.

Any failure in those checks means you should alert the user and not continue!

Note that the worktrees are frequently left behind on a *previous* dogfooding branch, with a stale
`ddtrace` pin still in `requirements.in`. "Clean" is not the same as "up to date" — always reset
explicitly to the repo's default branch before editing, and never build a new dogfooding change on
top of an old one.

# General instructions

During the workflow, if _anything_ goes unexpected (i.e. a command fails, etc.) do NOT try
to fix the problem yourself. Instead, you should notify the user and ask for guidance.

# Finding the dogfooding build

Dogfooding wheels for every `main` commit are
published continuously to ADMS, so in the common case there is nothing to build — just pick the
newest one.

List the published dogfooding versions, newest first:

```sh
curl -s "https://depot-read-api-python.us1.ddbuild.io/datadog/pypi-private-prereleases/@current/simple/ddtrace/" \
  | grep -oE '[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?\.dev[0-9]+\+internal\.dogfooding\.[0-9a-f]{40}' \
  | sort -u | sed -E 's/^(.*\.dev([0-9]+)\+.*)$/\2 \1/' | sort -rn | head -5 | cut -d' ' -f2-
```

Versions look like `<version>.dev<pipeline-id>+internal.dogfooding.<commit-sha>`, for example:

```
4.16.1.dev145585446+internal.dogfooding.5fa20c605391c5fa0694c2c6dca15040ef0aea5e
4.17.0rc1.dev145585442+internal.dogfooding.3364f794ad17bc7f17d7cbac90495c4992e890fb
```

Two things to watch for:
- **Several release lines are published in parallel** (e.g. `4.16.1` from a release branch and
  `4.17.0rc1` from `main`). Sorting by pipeline ID alone will mix them. Pick the one matching the
  branch you actually want to dogfood — normally the highest minor version, which is `main`.
- **Verify the embedded commit SHA** resolves to a commit on `origin/main` of `dd-trace-py`, and
  tell the user which commit they are about to dogfood. The SHA is the real identity of the
  build; the pipeline ID is not.

If the user names a specific version, use it as-is and just verify its SHA — don't go looking for
a newer one.

## Fallback: cutting an internal release by hand

Only do this when the user needs a build of something that is *not* on `main` (a PR branch, a local
fix). Otherwise use the ADMS lookup above.

Reset the `kowalski/internal-dogfooding` branch (if it doesn't exist, create it) to latest `main`:

```sh
git fetch origin main
git checkout origin/main
git checkout -B kowalski/internal-dogfooding
```

Update the version in `pyproject.toml` to have this local segment.

```diff
 [project]
 name = "ddtrace"
-version = "4.17.0rc1"
+version = "4.17.0rc1+internal-dogfooding"
 description = "Datadog APM client library"
```

Commit the changes and push it.

```sh
git add pyproject.toml
git commit -m "Set pyproject version to internal-dogfooding"
git push -f
```

Note: force-pushing this branch is OK -- it is only used for my purposes of internal dogfooding.
Even if we accidentally force-push something that shouldn't have been, we can recover it from
GitHub history, so don't worry about that.

This triggers a CI pipeline on `https://gitlab.ddbuild.io/DataDog/apm-reliability/dd-trace-py`
that builds internal dogfooding wheels.

Search for the CI pipeline it creates, specifically the "patch wheels" for `manylinux` under
`package`. When you find it, please print the link to both jobs (even if they're not started yet).
Note the pipeline is mirrored from GitHub, so it does not appear immediately — poll the API by
commit SHA until it shows up.

Wait for the jobs to start and finish, then let me know when it's done.
Check at most every thirty seconds, but more often if you think it's going to end soon.

The "patch wheels" jobs will output the name of the internal dogfooding release. Make sure the
commit SHA matches the one you pushed for the `kowalski/internal-dogfooding` branch.

# How to deploy the changes to `dogweb`

## Updating requirements

Go to the `dogweb` worktree I gave you and reset it to the latest default branch. Remember this
repo's default branch is `prod`.

```sh
git fetch origin prod
git checkout origin/prod
```

Update the `ddtrace` version in `requirements.in` to the one you found, example:

```diff
-ddtrace==4.16.0rc1
+ddtrace==4.17.0rc1.dev145585442+internal.dogfooding.3364f794ad17bc7f17d7cbac90495c4992e890fb
 decorator==5.3.1
 defusedxml==0.7.1
```

Then, run `rake python:compile_requirements` to update the requirements files.
This takes about 30s and should not fail. It rewrites `requirements3.txt` as well as
`requirements.in`.

Before committing, check that `ddtrace` is the only package pin that changed:

```sh
git diff | grep -E "^[-+][a-zA-Z0-9_.-]+==" | sed -E 's/==.*//' | sort -u
```

If anything other than `ddtrace` appears, stop and tell the user. (Note that grepping the log for
"error" gives false positives — `rapid-apierrors` and `dashboards-errors-lib` are real package
names.)

Once this is finished, stage the changes to all requirements files and commit them on a new
branch.

```sh
git checkout --detach
git add requirements*
git commit -m "[requirements] Use ddtrace internal dogfooding <include details here>"
git checkout -b kowalski/ddtrace-dogfooding-<insert_date_here>-<insert_ddtrace_commit_sha_here>
git push
```

Once that is done, use `gh` to create a PR from the branch.
- This PR should be a draft, not really open.
- The PR should be named "[requirements] Use ddtrace `<version>`", where `<version>` is the
  base version (without the `.dev<pipeline-id>` and `+internal.dogfooding` parts) followed by
  `+` and the first 12 characters of the commit SHA, e.g. `4.15.0rc1+abcdefabcdef`.
- The PR description should contain only the following, and nothing else (no summary, no notes
  like "only the ddtrace pin changes"):

  ```
  Upgrade ddtrace to `<full version>`

  https://github.com/DataDog/dd-trace-py/commit/<full commit sha>
  ```

  where `<full version>` is the complete version string, including the pipeline ID and full SHA,
  e.g. `4.17.0rc1.dev145585442+internal.dogfooding.3364f794ad17bc7f17d7cbac90495c4992e890fb`.
- The PR should target `prod`.
- Do NOT include "Generated with Claude Code" (or any other AI attribution) in the PR title,
  description, or commit messages.

## Sending changes to `staging`

If the user wants to test in staging, add a comment on the PR containing exactly...

```
/to-staging
```

This will trigger a workflow that will integrate the dependency update.

# How to deploy the changes in `dd-source`

## Updating requirements

Go to the `dd-source` worktree I gave you and checkout the latest `origin/main`.

```
git fetch origin main
git checkout origin/main
```

Update the `ddtrace` version in `requirements.in` to the one you found, example:

```diff
-ddtrace==4.16.0rc2
+ddtrace==4.17.0rc1.dev145585442+internal.dogfooding.3364f794ad17bc7f17d7cbac90495c4992e890fb
 ddtrace-api==0.0.1
 decorator==5.3.1
```

Note there is also a `ddtrace-api` pin right next to it — leave that one alone.

Then, run `bzl run //:requirements.update` to update the requirements files. This takes about a
minute. Two things commonly go wrong, and you must watch the output for both:

- **`bzl` too old.** It prints `bzl is too old to run in this repository` and refuses to do
  anything. Do not retry it and do not invoke `bazel`/`bazelisk` directly — the tool explicitly
  tells you not to. Report it and ask the user before upgrading.
- **OIDC / Vault authentication.** It prints a device-login prompt that *blocks the build*:

  ```
  Complete the login via your OIDC provider. Open the following link in your browser:
      https://www.google.com/device
  Waiting for OIDC authentication to complete...
  When prompted, enter code XXXX-XXXX
  ```

  **Tell the user immediately and relay the code** — device codes expire in minutes. Watch the log
  rather than waiting silently for the command to exit.

  If nobody completes the login, it fails with `Vault login failed. Expired or missing OAuth
  state.` and warns `Build will likely fail or hang`. Despite that warning the build often still
  **succeeds** off the local/remote cache. Treat a success here as unverified against the live
  index: confirm the lockfile really picked up the new version before trusting it.

Verify the result the same way as for dogweb — `ddtrace` should be the only package pin that
changed in `requirements.txt`:

```sh
git diff origin/main HEAD -- requirements.txt | grep -E "^[-+][a-zA-Z0-9_.-]+==" | sed -E 's/==.*//' | sort -u
```

Expect a fairly large line count in that file even for a one-package change (~80 lines), because
the dogfooding wheel ships a different set of platform hashes and `# via` comments than a released
version. That churn is benign; a second changed *package pin* is not.

Once this is finished, stage the changes to all requirements files and commit them on a new
branch.

```sh
git checkout --detach
git add requirements*
git commit -m "[requirements] Use ddtrace internal dogfooding <include details here>"
git checkout -b kowalski/ddtrace-dogfooding-<insert_date_here>-<insert_ddtrace_commit_sha_here>
git push
```

Once that is done, use `gh` to create a PR from the branch.
- This PR should be a draft, not really open.
- The PR should be named "[requirements] Use ddtrace `<version>`", where `<version>` is the
  base version (without the `.dev<pipeline-id>` and `+internal.dogfooding` parts) followed by
  `+` and the first 12 characters of the commit SHA, e.g. `4.15.0rc1+abcdefabcdef`.
- The PR description should contain only the following, and nothing else (no summary, no notes
  like "only the ddtrace pin changes"):

  ```
  Upgrade ddtrace to `<full version>`

  https://github.com/DataDog/dd-trace-py/commit/<full commit sha>
  ```

  where `<full version>` is the complete version string, including the pipeline ID and full SHA,
  e.g. `4.17.0rc1.dev145585442+internal.dogfooding.3364f794ad17bc7f17d7cbac90495c4992e890fb`.
- The PR should target `main`
- Do NOT include "Generated with Claude Code" (or any other AI attribution) in the PR title,
  description, or commit messages.

At this point, give me a heads-up!

## Sending the changes to smoke tests

Do the following if the user wants to test the new version on smoke tests.

Create a Rapid Test Drive for HTTP Smoke Tests

```sh
rapid td create -s rapid_python_http_smoke_test
```

Then create a Rapid Test Drive for gRPC Smoke Tests

```sh
rapid td create -s rapid_python_grpc_smoke_test
```

## Sending the changes to a real service

If the user wants to test the changes in a "real" service in staging, follow these steps.

The user should tell you whether they want to send the changes to the service in staging
or in a Test Drive. If they don't, ask them for confirmation.

If the user wants to send the changes to a Test Drive, use

```sh
rapid td create -s <service_name>
```

If the user wants to send the changes to staging (shared integration branch), use

```sh
# Before running this, always ensure a PR already exists for the branch
ddr devflow --no-interactive integrate -s <service_name>
# If the command asks whether you want to push the local changes, it's a bug
# don't push anything!
```

Note that if a previous dogfooding branch has already been merged to the integration
branch, this will result in conflicts and the `integrate` workflow will fail.  
If that happens, remove the previous branch from integration 

```sh
ddr devflow --no-interactive code remove-from-staging --pr <pull_request_number>
```

When using `ddr devflow integrate`, if conflicts appear, try to discover what the
conflicts are and to fix them yourself.
