import contextlib
import json
from multiprocessing import Process
import os
import random
import tempfile

import tenacity

from ddtrace.ext.git import extract_commit_sha
from ddtrace.ext.git import extract_latest_commits
from ddtrace.ext.git import extract_remote_url
from ddtrace.ext.git import get_rev_list_excluding_commits
from ddtrace.ext.git import git_subprocess_cmd

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
        if rev_list:
            with cls._build_packfiles(rev_list, cwd=cwd) as packfiles_path:
                cls._upload_packfiles(repo_url, packfiles_path, serde, cwd=cwd)

    @classmethod
    def _get_repository_url(cls, cwd=None):
        return extract_remote_url(cwd=cwd)

    @classmethod
    def _get_latest_commits(cls, cwd=None):
        return extract_latest_commits(cwd=cwd)

    @classmethod
    def _search_commits(cls, repo_url, latest_commits, serde):
        payload = serde.search_commits_encode(repo_url, latest_commits)
        response = cls.retry_request()(cls._do_request, "/search_commits", payload, serde=serde)
        result = serde.search_commits_decode(response.body)
        return result

    @classmethod
    def _do_request(cls, endpoint, payload, headers=None, timeout=None, serde=None):
        url = "https://git-api-ci-app-backend.us1.staging.dog/repository%s" % endpoint
        _headers = {"dd-api-key": serde.api_key, "dd-application-key": serde.app_key}
        if headers is not None:
            _headers.update(headers)
        try:
            conn = get_connection(url, timeout)
            conn.request("POST", url, payload, _headers)  # type: ignore[attr-defined]
            resp = compat.get_connection_response(conn)
            return Response.from_http_response(resp)
        finally:
            conn.close()

    @classmethod
    def _get_filtered_revisions(cls, excluded_commits, cwd=None):
        return get_rev_list_excluding_commits(excluded_commits, cwd=cwd)

    @classmethod
    @contextlib.contextmanager
    def _build_packfiles(cls, revisions, cwd=None):
        basename = str(random.randint(1, 1000000))
        with tempfile.TemporaryDirectory() as tempdir:
            path = "{tempdir}/{basename}".format(tempdir=tempdir, basename=basename)
            git_subprocess_cmd("pack-objects --compression=9 --max-pack-size=3m %s" % path, cwd=cwd, std_in=revisions)
            yield path

    @classmethod
    def _upload_packfiles(cls, repo_url, packfiles_path, serde, cwd=None):
        sha = extract_commit_sha(cwd=cwd)
        directory, rand = packfiles_path.rsplit("/", maxsplit=1)
        for filename in os.listdir(directory):
            if not filename.startswith(rand):
                continue
            file_path = os.path.join(directory, filename)
            content_type, payload = serde.upload_packfile_encode(repo_url, sha, file_path)
            headers = {"Content-Type": content_type}
            response = cls.retry_request()(cls._do_request, "/packfile", payload, headers=headers, serde=serde)
            return response.status == 204

    @classmethod
    def retry_request(cls):
        return tenacity.Retrying(
            wait=tenacity.wait_random_exponential(
                multiplier=(0.618 / (1.618 ** cls.RETRY_ATTEMPTS) / 2),  # type: ignore[attr-defined]
                exp_base=1.618,
            ),
            stop=tenacity.stop_after_attempt(cls.RETRY_ATTEMPTS),  # type: ignore[attr-defined]
            retry=tenacity.retry_if_exception_type((compat.httplib.HTTPException, OSError, IOError)),
        )


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

    def upload_packfile_encode(self, repo_url, sha, file_path):
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
                b'Content-Disposition: form-data; name="file"; filename="%s"' % file_name.encode("utf-8"),
                b"Content-Type: application/octet-stream",
                b"",
                file_content,
            ]
        )
        body.extend([b"--" + BOUNDARY + b"--", b""])
        return "multipart/form-data; boundary=%s" % BOUNDARY, CRLF.join(body)
