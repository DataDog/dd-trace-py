# What this is about

We want to dogfood the latest version of `dd-trace-py` in our internal repositories
dd-source and dogweb.

There are helpers to make that possible, but you will need to coordinate the various steps.

You are free to use the following worktrees:
- `dd-trace-py`: `/home/bits/go/src/github.com/DataDog/ddtpy-dogfooding`
- `dogweb`: `/home/bits/go/src/github.com/DataDog/dogweb-dogfooding`
- `dd-source`: `/home/bits/go/src/github.com/DataDog/dd-source-dogfooding`

# Pre-flight checks

Before you start working, please check that everything you will possibly need is available.
- Make sure the worktrees I listed exist and match my description
- Make sure the `dd-trace-py` worktree is clean (no unstaged changes, no new files)
- Make sure the `dogweb` worktree is clean (no unstaged changes, no new files)
- Make sure the `dd-source` workree is clean (no unstaged changes, no new files)
- Check that `ddtool auth gitlab token` gives you a GitLab token that you can use to query
  the API for `https://gitlab.ddbuild.io`.
- Check that the `rapid` CLI is available. If not, propose to install it using
  `brew install rapid` on macOS and `update-tool rapid` in workspaces.

# General instructions

During the workflow, if _anything_ goes unexpected (i.e. a command fails, etc.) do NOT try
to fix the problem yourself. Instead, you should notify the user and ask for guidance.

# How to cut an internal release from `main`

The CI for `dd-trace-py` makes it easy to create an internal release.
Simply follow these instructions.

Reset the `kowalski/internal-dogfooding` (if it doesn't exist, create it) to latest `main`

```sh
git fetch origin main
git checkout origin/main
git checkout -B kowalski/internal-dogfooding
```

Update the version in `pyproject.toml` to have this local segment.

```diff
diff --git a/pyproject.toml b/pyproject.toml
index d829389822..2288e34160 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -9,7 +9,7 @@ build-backend = "setuptools.build_meta"
 
 [project]
 name = "ddtrace"
-version = "4.15.0rc1"
+version = "4.15.0rc1+internal-dogfooding"
 description = "Datadog APM client library"
 readme = "README.md"
 license = { text = "LICENSE.BSD3" }
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

This will automatically trigger a CI pipeline that will build internal dogfooding wheels on our
GitLab instance: `https://gitlab.ddbuild.io/DataDog/apm-reliability/dd-trace-py`.

Search for the CI pipeline it creates, specifically the "patch wheels" for `manylinux` under `package`.
When you find it, please print the link to both jobs (even if they're not started yet).

Wait for the jobs to start and finish, then let me know when it's done.
Check at most every thirty seconds, but more often if you think it's going to end soon.

The "patch wheels" jobs will output the name of the internal dogfooding release, it typically
includes the pipeline ID and the commit SHA. Make sure the commit SHA matches the one you pushed
for the `kowalski/internal-dogfooding` branch.

Once you have found the new version name and the wheels have finished pushing, you can proceed to
the next step.

# How to deploy the changes to `dogweb`

Go to the `dogweb` worktree I gave you.

Update the `ddtrace` version in `requirements.in` to match the one you found in the job, example:

```diff
diff --git a/requirements.in b/requirements.in
index 06eaeaa3d819..411db3898331 100644
--- a/requirements.in
+++ b/requirements.in
@@ -380,7 +380,7 @@ crx3==0.0.4
 cryptography===50.0.0  # temporarily pinning for ADMS resolution
 datadog==0.52.1
 dash==3.3.0
-ddtrace==4.11.6
+ddtrace==4.15.0rc3.dev123456789+internal.dogfooding.abcdefabcdef
 decorator==5.2.1
 defusedxml==0.7.1
 debugpy==1.8.19
```

Then, run `rake python:compile_requirements` to update the requirements files.
This may take a while, but should not fail.

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
This PR should be a draft, not really open.

At this point, give me a heads-up!

# How to deploy the changes in `dd-source`

## Updating requirements

Go to the `dd-source` worktree I gave you and checkout the latest `origin/main`.

```
git fetch origin main
git checkout origin/main
```

Update the `ddtrace` version in `requirements.in` to match the one you found in the job, example:

```diff
diff --git a/requirements.in b/requirements.in
index 06eaeaa3d819..411db3898331 100644
--- a/requirements.in
+++ b/requirements.in
@@ -380,7 +380,7 @@ crx3==0.0.4
 cryptography===50.0.0  # temporarily pinning for ADMS resolution
 datadog==0.52.1
 dash==3.3.0
-ddtrace==4.11.6
+ddtrace==4.15.0rc3.dev123456789+internal.dogfooding.abcdefabcdef
 decorator==5.2.1
 defusedxml==0.7.1
 debugpy==1.8.19
```

Then, run `bzl run //:requirements.update` to update the requirements files.
This may take a while, but should not fail.

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
- The PR should be named "[ddtrace] Dogfood unreleased `<pipeline-id>-<commit_sha>`
- The PR should target `main`

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
ddr devflow integrate -s <service_name>
# If the command asks whether you want to push the local changes, it's a bug
# don't push anything!
```

When using `ddr devflow integrate`, if conflicts appear, try to discover what the
conflicts are and to fix them yourself.
