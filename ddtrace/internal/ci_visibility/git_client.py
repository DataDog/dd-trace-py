from multiprocessing import Process

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
        latest_commits = cls._get_latest_commits()
        backend_commits = cls._search_commits(repo_url, latest_commits)
        rev_list = cls._get_revisions(backend_commits)
        packfiles = cls._build_packfiles(rev_list)
        cls._upload_packfiles(packfiles)

    @classmethod
    def _get_repository_url(cls, cwd=None):
        return extract_remote_url(cwd=cwd) or "origin"

    @classmethod
    def _get_latest_commits(cls):
        pass

    @classmethod
    def _search_commits(cls, repo_url, latest_commits):
        pass

    @classmethod
    def _get_revisions(cls, backend_commits):
        pass

    @classmethod
    def _build_packfiles(cls, revisions):
        pass

    @classmethod
    def _upload_packfiles(cls, packfiles):
        pass
