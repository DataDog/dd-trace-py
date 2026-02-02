"""Shared utilities for coverage report uploads across V2 and V3 plugins."""

import time
import typing as t


def create_coverage_report_event(
    coverage_format: str,
    git_data: t.Optional[t.Dict[str, str]] = None,
    service: t.Optional[str] = None,
    env: t.Optional[str] = None,
    custom_tags: t.Optional[t.Dict[str, str]] = None,
) -> t.Dict[str, t.Any]:
    """
    Create the event JSON payload for coverage report upload.

    This function is used by both V2 (CI Visibility) and V3 (Testing) plugins
    to create a consistent event structure for coverage report uploads.

    Args:
        coverage_format: The format of the coverage report (e.g., 'lcov', 'cobertura')
        git_data: Dictionary containing git information (repository_url, commit_sha, branch, etc.)
        service: The service name
        env: The environment name
        custom_tags: Optional additional tags to include in the event

    Returns:
        Dictionary with event data ready for upload
    """
    event: t.Dict[str, t.Any] = {
        "type": "coverage_report",
        "format": coverage_format,
        "timestamp": int(time.time() * 1000),
    }

    # Add custom tags first (can be overridden by structured data)
    if custom_tags:
        event.update(custom_tags)

    # Add git data
    if git_data:
        # Common git fields that both V2 and V3 use
        git_field_mapping = {
            "git.repository_url": "git.repository_url",
            "repository_url": "git.repository_url",
            "git.commit.sha": "git.commit.sha",
            "commit_sha": "git.commit.sha",
            "git.branch": "git.branch",
            "branch": "git.branch",
            "git.tag": "git.tag",
            "tag": "git.tag",
            "git.commit.message": "git.commit.message",
            "commit_message": "git.commit.message",
            "git.commit.author.name": "git.commit.author.name",
            "commit_author_name": "git.commit.author.name",
            "git.commit.author.email": "git.commit.author.email",
            "commit_author_email": "git.commit.author.email",
            "git.commit.author.date": "git.commit.author.date",
            "commit_author_date": "git.commit.author.date",
            "git.commit.committer.name": "git.commit.committer.name",
            "commit_committer_name": "git.commit.committer.name",
            "git.commit.committer.email": "git.commit.committer.email",
            "commit_committer_email": "git.commit.committer.email",
            "git.commit.committer.date": "git.commit.committer.date",
            "commit_committer_date": "git.commit.committer.date",
        }

        for source_key, target_key in git_field_mapping.items():
            if source_key in git_data and git_data[source_key]:
                event[target_key] = git_data[source_key]

        # Handle PR number special case
        if "git.pull_request.number" in git_data:
            event["pr.number"] = git_data["git.pull_request.number"]

    # Add service and environment
    if service:
        event["service"] = service
    if env:
        event["env"] = env

    return event
