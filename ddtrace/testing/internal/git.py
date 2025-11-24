from dataclasses import dataclass
import logging
from pathlib import Path
import random
import re
import shutil
import subprocess
import tempfile
import typing as t


log = logging.getLogger(__name__)


class GitTag:
    # Git Repository URL
    REPOSITORY_URL = "git.repository_url"

    # Git Commit SHA
    COMMIT_SHA = "git.commit.sha"

    # Git Branch
    BRANCH = "git.branch"

    # Git Tag
    TAG = "git.tag"

    # Git Commit Message
    COMMIT_MESSAGE = "git.commit.message"

    # Git Commit Author Name
    COMMIT_AUTHOR_NAME = "git.commit.author.name"

    # Git Commit Author Email
    COMMIT_AUTHOR_EMAIL = "git.commit.author.email"

    # Git Commit Author Date (UTC)
    COMMIT_AUTHOR_DATE = "git.commit.author.date"

    # Git Commit Committer Name
    COMMIT_COMMITTER_NAME = "git.commit.committer.name"

    # Git Commit Committer Email
    COMMIT_COMMITTER_EMAIL = "git.commit.committer.email"

    # Git Commit Committer Date (UTC)
    COMMIT_COMMITTER_DATE = "git.commit.committer.date"

    # Git Commit HEAD SHA
    COMMIT_HEAD_SHA = "git.commit.head.sha"

    # Git Commit HEAD message
    COMMIT_HEAD_MESSAGE = "git.commit.head.message"

    # Git Commit HEAD author date
    COMMIT_HEAD_AUTHOR_DATE = "git.commit.head.author.date"

    # Git Commit HEAD author email
    COMMIT_HEAD_AUTHOR_EMAIL = "git.commit.head.author.email"

    # Git Commit HEAD author name
    COMMIT_HEAD_AUTHOR_NAME = "git.commit.head.author.name"

    # Git Commit HEAD committer date
    COMMIT_HEAD_COMMITTER_DATE = "git.commit.head.committer.date"

    # Git Commit HEAD committer email
    COMMIT_HEAD_COMMITTER_EMAIL = "git.commit.head.committer.email"

    # Git Commit HEAD committer name
    COMMIT_HEAD_COMMITTER_NAME = "git.commit.head.committer.name"


@dataclass
class _GitSubprocessDetails:
    stdout: str
    stderr: str
    return_code: int


@dataclass
class GitUserInfo:
    author_name: str
    author_email: str
    author_date: str
    committer_name: str
    committer_email: str
    committer_date: str


class Git:
    def __init__(self, cwd: t.Optional[str] = None):
        git_command = shutil.which("git")
        if not git_command:
            # Raise this at instantiation time, so that if an instance is successfully initialized, that means `git` is
            # available and we don't have to check for it every time.
            raise RuntimeError("`git` command not found")

        self.git_command: str = git_command
        self.cwd = cwd

    def _call_git(self, args: t.List[str], input_string: t.Optional[str] = None) -> _GitSubprocessDetails:
        git_cmd = [self.git_command, *args]
        log.debug("Running git command: %r", git_cmd)

        process = subprocess.Popen(
            git_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            cwd=self.cwd,
            encoding="utf-8",
            errors="surrogateescape",
        )
        stdout, stderr = process.communicate(input=input_string)

        return _GitSubprocessDetails(stdout=stdout.strip(), stderr=stderr.strip(), return_code=process.returncode)

    def _git_output(self, args: t.List[str]) -> str:
        result = self._call_git(args)
        if result.return_code != 0:
            log.warning("Error calling git %s: %s", " ".join(args), result.stderr)
            return ""
        return result.stdout

    def get_git_version(self) -> t.Tuple[int, ...]:
        output = self._git_output(["--version"])  # "git version 1.2.3"
        try:
            version_string = output.split()[2]
            version_tuple = tuple(int(part) for part in version_string.split("."))
            return version_tuple
        except (IndexError, ValueError):
            log.error("Could not parse git --version output: %s", output)
            return (0, 0, 0)

    def get_repository_url(self) -> str:
        return self._git_output(["ls-remote", "--get-url"])

    def get_commit_sha(self) -> str:
        return self._git_output(["rev-parse", "HEAD"])

    def get_upstream_sha(self) -> str:
        return self._git_output(["rev-parse", "@{upstream}"])

    def get_branch(self) -> str:
        return self._git_output(["rev-parse", "--abbrev-ref", "HEAD"])

    def get_commit_message(self, commit_sha: t.Optional[str] = None) -> str:
        command = ["show", "-s", "--format=%s"]
        if commit_sha:
            command.append(commit_sha)

        return self._git_output(command)

    def get_user_info(self, commit_sha: t.Optional[str] = None) -> t.Optional[GitUserInfo]:
        command = ["show", "-s", "--format=%an|||%ae|||%ad|||%cn|||%ce|||%cd", "--date=format:%Y-%m-%dT%H:%M:%S%z"]
        if commit_sha:
            command.append(commit_sha)

        output = self._git_output(command)
        if not output:
            return None

        author_name, author_email, author_date, committer_name, committer_email, committer_date = output.split("|||")
        return GitUserInfo(
            author_name=author_name,
            author_email=author_email,
            author_date=author_date,
            committer_name=committer_name,
            committer_email=committer_email,
            committer_date=committer_date,
        )

    def get_workspace_path(self) -> str:
        return self._git_output(["rev-parse", "--show-toplevel"])

    def get_remote_name(self) -> str:
        return self._git_output(["config", "--default", "origin", "--get", "clone.defaultRemoteName"])

    def get_latest_commits(self) -> t.List[str]:
        output = self._git_output(["log", "--format=%H", "-n", "1000", '--since="1 month ago"'])
        return output.split("\n") if output else []

    def get_filtered_revisions(self, excluded_commits: t.List[str], included_commits: t.List[str]) -> t.List[str]:
        exclusions = [f"^{sha}" for sha in excluded_commits]
        output = self._git_output(
            [
                "rev-list",
                "--objects",
                "--filter=blob:none",
                '--since="1 month ago"',
                "--no-object-names",
                "HEAD",
                *exclusions,
                *included_commits,
            ]
        )
        return output.split("\n")

    def is_shallow_repository(self) -> bool:
        output = self._git_output(["rev-parse", "--is-shallow-repository"])
        return output == "true"

    def unshallow_repository(self, refspec: t.Optional[str] = None, parent_only: bool = False) -> bool:
        remote_name = self.get_remote_name()
        command = [
            "fetch",
            "--deepen=1" if parent_only else '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "--no-tags",
            remote_name,
        ]
        if refspec:
            command.append(refspec)

        result = self._call_git(command)
        if result.return_code != 0:
            log.warning("Error unshallowing repo for refspec %s: %s", refspec, result.stderr)
        return result.return_code == 0

    def unshallow_repository_to_local_head(self) -> bool:
        return self.unshallow_repository(self.get_commit_sha())

    def unshallow_repository_to_upstream(self) -> bool:
        upstream_sha = self.get_upstream_sha()
        if not upstream_sha:
            log.warning("Error unshallowing repo to upstream: no upstream sha")
            return False

        return self.unshallow_repository(self.get_upstream_sha())

    def unshallow_repository_to_default(self) -> bool:
        return self.unshallow_repository(None)

    def try_all_unshallow_repository_methods(self) -> bool:
        if self.unshallow_repository_to_local_head():
            return True

        if self.unshallow_repository_to_upstream():
            return True

        if self.unshallow_repository_to_default():
            return True

        log.debug("Unshallow failed")
        return False

    def pack_objects(self, revisions: t.List[str]) -> t.Iterable[Path]:
        base_name = str(random.randint(1, 1000000))
        revisions_text = "\n".join(revisions)

        cwd = Path(self.cwd) if self.cwd is not None else Path.cwd()
        temp_dir_base = Path(tempfile.gettempdir())
        if cwd.stat().st_dev != temp_dir_base.stat().st_dev:
            # `git pack-objects` does not work properly when the target is in a different device.
            # In this case, we create the temporary directory in the current directory as a fallback.
            temp_dir_base = cwd

        with tempfile.TemporaryDirectory(dir=temp_dir_base) as output_dir:
            prefix = f"{output_dir}/{base_name}"
            result = self._call_git(["pack-objects", "--compression=9", "--max-pack-size=3m", prefix], revisions_text)
            if result.return_code != 0:
                log.warning("Error calling git pack-objects: %s", result.stderr)
                return None

            for packfile in Path(output_dir).glob(f"{base_name}*.pack"):
                yield packfile


def get_git_tags_from_git_command() -> t.Dict[str, t.Optional[str]]:
    try:
        git = Git()
    except RuntimeError as e:
        log.warning("Error getting git data: %s", e)
        return {}

    tags: t.Dict[str, t.Optional[str]] = {
        GitTag.REPOSITORY_URL: git.get_repository_url(),
        GitTag.COMMIT_SHA: git.get_commit_sha(),
        GitTag.BRANCH: git.get_branch(),
        GitTag.COMMIT_MESSAGE: git.get_commit_message(),
    }

    if user_info := git.get_user_info():
        tags.update(
            {
                GitTag.COMMIT_AUTHOR_NAME: user_info.author_name,
                GitTag.COMMIT_AUTHOR_EMAIL: user_info.author_email,
                GitTag.COMMIT_AUTHOR_DATE: user_info.author_date,
                GitTag.COMMIT_COMMITTER_NAME: user_info.committer_name,
                GitTag.COMMIT_COMMITTER_EMAIL: user_info.committer_email,
                GitTag.COMMIT_COMMITTER_DATE: user_info.committer_date,
            }
        )

    return tags


def get_git_head_tags_from_git_command(head_sha: str) -> t.Dict[str, t.Optional[str]]:
    try:
        git = Git()
    except RuntimeError as e:
        log.warning("Error getting git data: %s", e)
        return {}

    if git.is_shallow_repository():
        git.unshallow_repository(parent_only=True)

    tags: t.Dict[str, t.Optional[str]] = {
        GitTag.COMMIT_HEAD_MESSAGE: git.get_commit_message(head_sha),
    }

    if user_info := git.get_user_info(head_sha):
        tags.update(
            {
                GitTag.COMMIT_HEAD_AUTHOR_NAME: user_info.author_name,
                GitTag.COMMIT_HEAD_AUTHOR_EMAIL: user_info.author_email,
                GitTag.COMMIT_HEAD_AUTHOR_DATE: user_info.author_date,
                GitTag.COMMIT_HEAD_COMMITTER_NAME: user_info.committer_name,
                GitTag.COMMIT_HEAD_COMMITTER_EMAIL: user_info.committer_email,
                GitTag.COMMIT_HEAD_COMMITTER_DATE: user_info.committer_date,
            }
        )

    return tags


def get_workspace_path() -> Path:
    try:
        return Path(Git().get_workspace_path()).absolute()
    except RuntimeError:
        return Path.cwd()


_RE_REFS = re.compile(r"^refs/(heads/)?")
_RE_ORIGIN = re.compile(r"^origin/")
_RE_TAGS = re.compile(r"^tags/")


def normalize_ref(name: t.Optional[str]) -> t.Optional[str]:
    return _RE_TAGS.sub("", _RE_ORIGIN.sub("", _RE_REFS.sub("", name))) if name is not None else None


def is_ref_a_tag(ref: t.Optional[str]) -> bool:
    return "tags/" in ref if ref else False


def get_git_tags_from_dd_variables(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract git commit metadata from user-provided env vars."""
    branch = normalize_ref(env.get("DD_GIT_BRANCH"))
    tag = normalize_ref(env.get("DD_GIT_TAG"))

    # if DD_GIT_BRANCH is a tag, we associate its value to TAG instead of BRANCH
    if is_ref_a_tag(env.get("DD_GIT_BRANCH")):
        tag = branch
        branch = None

    tags: t.Dict[str, t.Optional[str]] = {
        GitTag.REPOSITORY_URL: env.get("DD_GIT_REPOSITORY_URL"),
        GitTag.COMMIT_SHA: env.get("DD_GIT_COMMIT_SHA"),
        GitTag.BRANCH: branch,
        GitTag.TAG: tag,
        GitTag.COMMIT_MESSAGE: env.get("DD_GIT_COMMIT_MESSAGE"),
        GitTag.COMMIT_AUTHOR_DATE: env.get("DD_GIT_COMMIT_AUTHOR_DATE"),
        GitTag.COMMIT_AUTHOR_EMAIL: env.get("DD_GIT_COMMIT_AUTHOR_EMAIL"),
        GitTag.COMMIT_AUTHOR_NAME: env.get("DD_GIT_COMMIT_AUTHOR_NAME"),
        GitTag.COMMIT_COMMITTER_DATE: env.get("DD_GIT_COMMIT_COMMITTER_DATE"),
        GitTag.COMMIT_COMMITTER_EMAIL: env.get("DD_GIT_COMMIT_COMMITTER_EMAIL"),
        GitTag.COMMIT_COMMITTER_NAME: env.get("DD_GIT_COMMIT_COMMITTER_NAME"),
    }

    return tags
