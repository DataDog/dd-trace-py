import dataclasses
import typing as t

from ddtrace.ext import ci


@dataclasses.dataclass(frozen=True)
class GitData:
    repository_url: t.Optional[str]
    branch: t.Optional[str]
    commit_sha: t.Optional[str]
    commit_message: t.Optional[str]


def get_git_data_from_tags(tags: t.Dict[str, t.Any]) -> GitData:
    return GitData(
        repository_url=tags.get(ci.git.REPOSITORY_URL),
        branch=tags.get(ci.git.BRANCH) or tags.get(ci.git.TAG),
        commit_sha=tags.get(ci.git.COMMIT_SHA),
        commit_message=tags.get(ci.git.COMMIT_MESSAGE),
    )
