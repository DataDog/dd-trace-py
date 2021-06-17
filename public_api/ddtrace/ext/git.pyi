from typing import Any, Dict, Optional, Tuple

GitNotFoundError = FileNotFoundError
BRANCH: str
COMMIT_SHA: str
REPOSITORY_URL: str
TAG: str
COMMIT_AUTHOR_NAME: str
COMMIT_AUTHOR_EMAIL: str
COMMIT_AUTHOR_DATE: str
COMMIT_COMMITTER_NAME: str
COMMIT_COMMITTER_EMAIL: str
COMMIT_COMMITTER_DATE: str
COMMIT_MESSAGE: str
log: Any

def extract_user_info(cwd: Optional[str]=...) -> Dict[str, Tuple[str, str, str]]: ...
def extract_repository_url(cwd: Optional[str]=...) -> str: ...
def extract_commit_message(cwd: Optional[str]=...) -> str: ...
def extract_git_metadata(cwd: Optional[str]=...) -> Dict[str, Optional[str]]: ...
