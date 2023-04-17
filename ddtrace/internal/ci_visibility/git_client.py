import json
import logging
from multiprocessing import Process

import tenacity

from ddtrace.ext.git import extract_latest_commits
from ddtrace.ext.git import extract_remote_url
from ddtrace.ext.git import get_rev_list_excluding_commits

from .. import compat
from ..utils.http import Response
from ..utils.http import get_connection


class CIVisibilityGitClient(object):
    RETRY_ATTEMPTS = 5

    def __init__(
        self,
        api_key,
        app_key,
    ):
        self._serde = CIVisibilityGitClientSerDeV1(api_key, app_key)

    def start(self, cwd=None):
        self._worker = Process(target=CIVisibilityGitClient._run_protocol, kwargs={"cwd": cwd, "serde": self._serde})
        self._worker.start()

    def shutdown(self, timeout=None):
        self._worker.join(timeout)
        self._worker = None

    @classmethod
    def _run_protocol(cls, cwd=None, serde=None):
        repo_url = cls._get_repository_url(cwd=cwd)
        latest_commits = cls._get_latest_commits(cwd=cwd)
        backend_commits = cls._search_commits(repo_url, latest_commits, serde)
        rev_list = cls._get_filtered_revisions(backend_commits, cwd=cwd)
        packfiles = cls._build_packfiles(rev_list, cwd=cwd)
        cls._upload_packfiles(packfiles)

    @classmethod
    def _get_repository_url(cls, cwd=None):
        return extract_remote_url(cwd=cwd)

    @classmethod
    def _get_latest_commits(cls, cwd=None):
        return extract_latest_commits(cwd=cwd)

    @classmethod
    def _search_commits(cls, repo_url, latest_commits, serde):
        payload = serde.search_commits_encode(repo_url, latest_commits)
        headers = {"dd-api-key": serde.api_key, "dd-application-key": serde.app_key}
        retry_request = tenacity.Retrying(
            wait=tenacity.wait_random_exponential(
                multiplier=(0.618 / (1.618 ** cls.RETRY_ATTEMPTS) / 2),  # type: ignore[attr-defined]
                exp_base=1.618,
            ),
            stop=tenacity.stop_after_attempt(cls.RETRY_ATTEMPTS),  # type: ignore[attr-defined]
            retry=tenacity.retry_if_exception_type((compat.httplib.HTTPException, OSError, IOError)),
        )
        url = "https://git-api-ci-app-backend.us1.staging.dog/repository/search_commits"
        response = retry_request(CIVisibilityGitClient._do_request, url, payload, headers)
        result = serde.search_commits_decode(response.body)
        return result

    @classmethod
    def _do_request(cls, url, payload, headers, timeout=None):
        try:
            conn = get_connection(url, timeout)
            conn.request("POST", url, payload, headers)  # type: ignore[attr-defined]
            resp = compat.get_connection_response(conn)
            return Response.from_http_response(resp)
        finally:
            conn.close()

    @classmethod
    def _get_filtered_revisions(cls, excluded_commits, cwd=None):
        return get_rev_list_excluding_commits(excluded_commits, cwd=cwd)

    @classmethod
    def _build_packfiles(cls, revisions, cwd=None):
        pass

    @classmethod
    def _upload_packfiles(cls, packfiles):
        pass


class CIVisibilityGitClientSerDeV1(object):
    def __init__(self, api_key, app_key):
        self.api_key = api_key
        self.app_key = app_key

    def search_commits_encode(self, repo_url, latest_commits):
        return json.dumps(
            {"meta": {"repository_url": repo_url}, "data": [{"id": sha, "type": "commit"} for sha in latest_commits]}
        )

    def search_commits_decode(self, payload):
        parsed = json.loads(payload)
        return [item["id"] for item in parsed["data"] if item["type"] == "commit"]
