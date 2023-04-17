class CIVisibilityGitClient(object):
    def start(self):
        # start protocol sequence in subprocess
        self._worker = object()

    def shutdown(self):
        # kill subprocess
        self._worker = None

    def _run_protocol(self):
        repo_url = self._get_repository_url()
        latest_commits = self._get_latest_commits()
        backend_commits = self._search_commits(repo_url, latest_commits)
        rev_list = self._get_revisions(backend_commits)
        packfiles = self._build_packfiles(rev_list)
        self._upload_packfiles(packfiles)
