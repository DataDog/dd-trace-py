import json
from multiprocessing import Process
import os
from typing import Callable  # noqa
from typing import Optional  # noqa
from typing import Tuple  # noqa

import tenacity

from ddtrace.ext.git import build_git_packfiles
from ddtrace.ext.git import extract_commit_sha
from ddtrace.ext.git import extract_latest_commits
from ddtrace.ext.git import extract_remote_url
from ddtrace.ext.git import get_rev_list_excluding_commits
from ddtrace.internal.logger import get_logger

from .. import compat
from ..utils.http import Response
from ..utils.http import get_connection
from .constants import AGENTLESS_DEFAULT_SITE
from .constants import GIT_API_BASE_PATH


log = get_logger(__name__)

# this exists only for the purpose of mocking in tests
RESPONSE = None

# we're only interested in uploading .pack files
PACK_EXTENSION = ".pack"


class CIVisibilityGitClient(object):
    RETRY_ATTEMPTS = 5

    def __init__(
        self,
        api_key,
        app_key,
        base_url="",
    ):
        # type: (str, str, str) -> None
        self._serializer = CIVisibilityGitClientSerializerV1(api_key, app_key)
        self._worker = None  # type: Optional[Process]
        self._base_url = "https://api.{}{}".format(os.getenv("DD_SITE", AGENTLESS_DEFAULT_SITE), GIT_API_BASE_PATH)
        self._response = RESPONSE

    def start(self, cwd=None):
        # type: (Optional[str]) -> None
        if self._worker is None:
            self._worker = Process(
                target=CIVisibilityGitClient._run_protocol,
                args=(self._serializer, self._base_url, self._response),
                kwargs={"cwd": cwd},
            )
            self._worker.start()

    def shutdown(self, timeout=None):
        # type: (Optional[float]) -> None
        if self._worker is not None:
            self._worker.join(timeout)
            self._worker = None

    @classmethod
    def _run_protocol(cls, serializer, base_url, _response, cwd=None):
        # type: (CIVisibilityGitClientSerializerV1, str, Optional[Response], Optional[str]) -> None
        repo_url = cls._get_repository_url(cwd=cwd)
        latest_commits = cls._get_latest_commits(cwd=cwd)
        backend_commits = cls._search_commits(base_url, repo_url, latest_commits, serializer, _response)
        rev_list = cls._get_filtered_revisions(backend_commits, cwd=cwd)
        if rev_list:
            with cls._build_packfiles(rev_list, cwd=cwd) as packfiles_prefix:
                cls._upload_packfiles(base_url, repo_url, packfiles_prefix, serializer, _response, cwd=cwd)

    @classmethod
    def _get_repository_url(cls, cwd=None):
        # type: (Optional[str]) -> str
        return extract_remote_url(cwd=cwd)

    @classmethod
    def _get_latest_commits(cls, cwd=None):
        # type: (Optional[str]) -> list[str]
        return extract_latest_commits(cwd=cwd)

    @classmethod
    def _search_commits(cls, base_url, repo_url, latest_commits, serializer, _response):
        # type: (str, str, list[str], CIVisibilityGitClientSerializerV1, Optional[Response]) -> list[str]
        payload = serializer.search_commits_encode(repo_url, latest_commits)
        response = _response or cls.retry_request(base_url, "/search_commits", payload, serializer)
        result = serializer.search_commits_decode(response.body)
        return result

    @classmethod
    def _do_request(cls, base_url, endpoint, payload, serializer, headers=None):
        # type: (str, str, str, CIVisibilityGitClientSerializerV1, Optional[dict]) -> Response
        url = "{}/repository{}".format(base_url, endpoint)
        _headers = {"dd-api-key": serializer.api_key, "dd-application-key": serializer.app_key}
        if headers is not None:
            _headers.update(headers)
        try:
            conn = get_connection(url)
            log.debug("Sending request: %s %s %s %s", ("POST", url, payload, _headers))
            conn.request("POST", url, payload, _headers)
            resp = compat.get_connection_response(conn)
            log.debug("Response status: %s", resp.status)
            return Response.from_http_response(resp)
        finally:
            conn.close()

    @classmethod
    def _get_filtered_revisions(cls, excluded_commits, cwd=None):
        # type: (list[str], Optional[str]) -> list[str]
        return get_rev_list_excluding_commits(excluded_commits, cwd=cwd)

    @classmethod
    def _build_packfiles(cls, revisions, cwd=None):
        return build_git_packfiles(revisions, cwd=cwd)

    @classmethod
    def _upload_packfiles(cls, base_url, repo_url, packfiles_prefix, serializer, _response, cwd=None):
        # type: (str, str, str, CIVisibilityGitClientSerializerV1, Optional[Response], Optional[str]) -> bool
        sha = extract_commit_sha(cwd=cwd)
        parts = packfiles_prefix.split("/")
        directory = "/".join(parts[:-1])
        rand = parts[-1]
        for filename in os.listdir(directory):
            if not filename.startswith(rand) or not filename.endswith(PACK_EXTENSION):
                continue
            file_path = os.path.join(directory, filename)
            content_type, payload = serializer.upload_packfile_encode(repo_url, sha, file_path)
            headers = {"Content-Type": content_type}
            response = _response or cls.retry_request(base_url, "/packfile", payload, serializer, headers=headers)
            if response.status != 204:
                return False
        return True

    @classmethod
    def retry_request(cls, *args, **kwargs):
        return tenacity.Retrying(
            wait=tenacity.wait_random_exponential(
                multiplier=(0.618 / (1.618 ** cls.RETRY_ATTEMPTS) / 2),
                exp_base=1.618,
            ),
            stop=tenacity.stop_after_attempt(cls.RETRY_ATTEMPTS),
            retry=tenacity.retry_if_exception_type((compat.httplib.HTTPException, OSError, IOError)),
        )(cls._do_request, *args, **kwargs)


class CIVisibilityGitClientSerializerV1(object):
    def __init__(self, api_key, app_key):
        # type: (str, str) -> None
        self.api_key = api_key
        self.app_key = app_key

    def search_commits_encode(self, repo_url, latest_commits):
        # type: (str, list[str]) -> str
        return json.dumps(
            {"meta": {"repository_url": repo_url}, "data": [{"id": sha, "type": "commit"} for sha in latest_commits]}
        )

    def search_commits_decode(self, payload):
        # type: (str) -> list[str]
        parsed = json.loads(payload)
        return [item["id"] for item in parsed["data"] if item["type"] == "commit"]

    def upload_packfile_encode(self, repo_url, sha, file_path):
        # type: (str, str, str) -> Tuple[str, bytes]
        BOUNDARY = b"----------boundary------"
        CRLF = b"\r\n"
        body = []
        metadata = {"data": {"id": sha, "type": "commit"}, "meta": {"repository_url": repo_url}}
        body.extend(
            [
                b"--" + BOUNDARY,
                b'Content-Disposition: form-data; name="pushedSha"',
                b"Content-Type: application/json",
                b"",
                json.dumps(metadata).encode("utf-8"),
            ]
        )
        file_name = os.path.basename(file_path)
        f = open(file_path, "rb")
        file_content = f.read()
        f.close()
        body.extend(
            [
                b"--" + BOUNDARY,
                b'Content-Disposition: form-data; name="packfile"; filename="%s"' % file_name.encode("utf-8"),
                b"Content-Type: application/octet-stream",
                b"",
                file_content,
            ]
        )
        body.extend([b"--" + BOUNDARY + b"--", b""])
        return "multipart/form-data; boundary=%s" % BOUNDARY.decode("utf-8"), CRLF.join(body)
