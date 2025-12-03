# Nightly CI Monitoring for Critical Branches

## Overview

The nightly CI monitoring system automatically validates the most recent 3 minor version branches (e.g., 4.0, 3.19, 3.18) to ensure they remain releasable. This prevents the situation where older supported branches accumulate hidden CI failures that only surface when patches need to be released.

## How It Works

### 1. Branch Detection

Every night at 1 AM UTC, the system automatically detects the most recent 3 minor version branches **PLUS** the last minor of the previous major version:

- Uses `scripts/get_active_branches.py` to query remote branches
- Sorts branches by semantic version
- Returns top 3 (e.g., `["4.0", "3.19", "3.18"]`)
- **Additionally includes the last minor of the previous major** if not already in top 3
  - Example: If top 3 are `[4.3, 4.2, 4.1]` (all major 4.x), adds `3.19` (last of major 3.x)

This ensures coverage of both the latest releases AND the last release of the previous major version line, which may still be receiving security patches.

### 2. Comprehensive Validation

For each detected branch, the workflow runs:

- **Unit Tests** (~15-20 min): Core tracer functionality
- **Framework Tests** (~60-90 min): Django, Flask, FastAPI compatibility
- **Build Tests** (~30-40 min): Ensures branch can be released

Total runtime: ~3-4 hours per branch (parallelized)

### 3. Incident Management

When failures are detected:

1. **GitHub Issue Created**: Automatically creates issue with:
   - Branch name and failed jobs
   - Direct links to failure logs
   - Assigned to @DataDog/apm-core-python
   - Labeled: `ci-nightly-failure`, `ci`, `infrastructure`

2. **Deduplication**: Updates existing issues instead of creating duplicates

3. **Auto-Resolution**: Closes issues when branches pass again

4. **Slack Alert**: Sends notification to #apm-python channel

## Setup Requirements

### 1. GitHub Secrets

The workflow requires the following secret to be configured:

**`SLACK_BOT_TOKEN`** (Required for Slack notifications)

To set up:

1. Create a Slack App at https://api.slack.com/apps
2. Add bot token scopes: `chat:write`, `chat:write.public`
3. Install app to workspace
4. Copy Bot User OAuth Token
5. Add to GitHub: Settings → Secrets → Actions → New secret
   - Name: `SLACK_BOT_TOKEN`
   - Value: `xoxb-...`

### 2. Slack Channel ID

Update the workflow file with the actual #apm-python channel ID:

```bash
# Get channel ID
curl -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  https://slack.com/api/conversations.list | \
  jq '.channels[] | select(.name=="apm-python") | .id'
```

Then update `.github/workflows/nightly-branch-monitor.yml` line 210:
```yaml
channel-id: 'CHANNEL_ID_PLACEHOLDER'  # Replace with actual ID
```

### 3. GitHub CLI

The incident management script requires `gh` CLI:

- Already available in GitHub Actions runners
- For local testing: `brew install gh`

## Usage

### Automatic Nightly Runs

The workflow runs automatically every night at 1 AM UTC (9 PM ET / 6 PM PT).

No manual intervention required - it will:
- Detect active branches
- Run tests
- Create/update incidents
- Send alerts

### Manual Testing

Trigger a manual run for a specific branch:

```bash
# Via GitHub Actions UI: go to Actions tab → Nightly Branch CI Monitor → Run workflow

# Or via gh CLI:
gh workflow run nightly-branch-monitor.yml --ref main --field branch=3.19
```

### Monitoring Active Incidents

Check for active CI failures:

```bash
# List open incidents
gh issue list --label ci-nightly-failure --state open

# View specific incident
gh issue view <issue-number>
```

## Incident Response

When you receive a Slack alert:

1. **Review the GitHub Issue**: Click the link in the Slack message
2. **Examine Failed Jobs**: Click log links in the issue
3. **Determine Root Cause**:
   - Genuine failure? Fix the code on that branch
   - Flaky test? Add `@flaky` decorator or fix root cause
   - External dependency? Update or pin version
4. **Verify Fix**: Wait for next nightly run or trigger manually
5. **Auto-Resolution**: Issue closes automatically when branch passes

## Adjusting Monitored Branches

To change the number of monitored branches (before adding the previous major), edit `scripts/get_active_branches.py`:

```python
ACTIVE_BRANCH_COUNT = 3  # Change to 4, 5, etc.
```

Note: The system will always add the last minor of the previous major version on top of this count. So:
- `ACTIVE_BRANCH_COUNT = 3` → typically monitors 3-4 branches
- `ACTIVE_BRANCH_COUNT = 4` → typically monitors 4-5 branches

Example scenarios:
- If branches are `4.3, 4.2, 4.1, 4.0, 3.19, 3.18`:
  - With `ACTIVE_BRANCH_COUNT = 3`: monitors `[4.3, 4.2, 4.1, 3.19]` (4 branches)
  - Top 3 are all 4.x, so 3.19 is added as last of previous major

- If branches are `4.0, 3.19, 3.18`:
  - With `ACTIVE_BRANCH_COUNT = 3`: monitors `[4.0, 3.19, 3.18]` (3 branches)
  - Top 3 already includes 3.19, so no duplication

## Disabling the Workflow

Temporarily disable:
```bash
gh workflow disable nightly-branch-monitor.yml
```

Re-enable:
```bash
gh workflow enable nightly-branch-monitor.yml
```

## Troubleshooting

### Workflow Fails to Start

- Check GitHub Actions quota/limits
- Verify workflow file syntax: `yamllint .github/workflows/nightly-branch-monitor.yml`

### Branch Detection Returns Empty

- Verify branch naming follows pattern: `X.Y` (e.g., `3.19`, `4.0`)
- Check remote branches exist: `git ls-remote --heads origin 'refs/heads/[0-9]*.[0-9]*'`

### Incident Creation Fails

- Verify `gh` CLI is authenticated in workflow
- Check `GITHUB_TOKEN` has `issues: write` permission

### Slack Notifications Not Sent

- Verify `SLACK_BOT_TOKEN` secret is set
- Check channel ID is correct in workflow file
- Confirm bot is invited to #apm-python channel

## Maintenance

### Weekly Tasks

- Review open incidents (label: `ci-nightly-failure`)
- Triage flaky test patterns
- Verify workflow runs successfully

### Monthly Tasks

- Review incident metrics (time-to-resolution, frequency)
- Adjust test coverage if needed
- Update documentation if workflow changes

### Metrics to Track

- Number of active incidents
- Average time to resolution
- Most frequently failing branches
- Most frequently failing test suites

Query metrics:
```bash
# Count open incidents
gh issue list --label ci-nightly-failure --state open --json number | jq 'length'

# List recently resolved
gh issue list --label ci-nightly-failure --state closed --search "closed:>$(date -d '7 days ago' +%Y-%m-%d)"
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Workflow: nightly-branch-monitor.yml               │
│  ├─ detect-branches job                             │
│  │   └─ scripts/get_active_branches.py              │
│  ├─ validate-branch job (matrix: 3 branches)        │
│  │   ├─ Checkout branch                             │
│  │   ├─ Run unit tests                              │
│  │   ├─ Run framework tests                         │
│  │   ├─ Run build tests                             │
│  │   └─ Collect results                             │
│  └─ report-failures job                             │
│      ├─ scripts/create_ci_incident.py (create/update)│
│      └─ Send Slack notification                     │
└─────────────────────────────────────────────────────┘
```

## Files

- `.github/workflows/nightly-branch-monitor.yml` - Main workflow
- `scripts/get_active_branches.py` - Branch detection logic
- `scripts/create_ci_incident.py` - Incident management
- `docs/nightly-ci-monitoring.md` - This documentation

## Related Documentation

- [Contributing: Testing](contributing-testing.rst) - Test execution guide
- [Contributing: Release](contributing-release.rst) - Release process
- `.github/CODEOWNERS` - Team assignments
