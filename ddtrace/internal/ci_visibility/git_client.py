from multiprocessing import Process

from ddtrace.ext.git import extract_latest_commits
from ddtrace.ext.git import extract_remote_url


class CIVisibilityGitClient(object):
    def start(self, cwd=None):
        self._worker = Process(target=CIVisibilityGitClient._run_protocol, kwargs={"cwd": cwd})
        self._worker.start()

    def shutdown(self, timeout=None):
        self._worker.join(timeout)
        self._worker = None

    @classmethod
    def _run_protocol(cls, cwd=None):
        repo_url = cls._get_repository_url(cwd=cwd)
        latest_commits = cls._get_latest_commits(cwd=cwd)
        backend_commits = cls._search_commits(repo_url, latest_commits, cwd=cwd)
        rev_list = cls._get_revisions(backend_commits, cwd=cwd)
        packfiles = cls._build_packfiles(rev_list, cwd=cwd)
        cls._upload_packfiles(packfiles, cwd=cwd)

    @classmethod
    def _get_repository_url(cls, cwd=None):
        return extract_remote_url(cwd=cwd) or "origin"

    @classmethod
    def _get_latest_commits(cls, cwd=None):
        return extract_latest_commits(cwd=cwd)

    @classmethod
    def _search_commits(cls, repo_url, latest_commits, cwd=None):
        pass

    @classmethod
    def _get_revisions(cls, backend_commits, cwd=None):
        pass

    @classmethod
    def _build_packfiles(cls, revisions, cwd=None):
        pass

    @classmethod
    def _upload_packfiles(cls, packfiles, cwd=None):
        pass
