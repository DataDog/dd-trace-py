import os
from typing import Dict
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class SearchResult(object):
    def __init__(self, match, cost):
        # type: (str, int) -> None
        self.match = match
        self.cost = cost

    def __str__(self):
        return "<SearchResult(match=%s,cost=%s)>" % (
            self.match,
            self.cost,
        )


def min_cost_match(a, b):
    # type: (Optional[SearchResult], Optional[SearchResult]) -> Optional[SearchResult]
    if a is None:
        return b
    elif b is None:
        return a
    if a.cost > b.cost:
        return b
    return a


class Trie(object):
    def __init__(self):
        # type: () -> None
        self.match = None  # type: Optional[str]
        self.branches = dict()  # type: Dict[str, Trie]

    def __repr__(self):
        return "<Trie(match=%s,branches=%s)>" % (
            self.match,
            self.branches,
        )

    def insert(self, s):
        # type: (str) -> None
        current_node = self
        for c in s:
            if c not in current_node.branches:
                next_node = Trie()
                current_node.branches[c] = next_node
            current_node = current_node.branches[c]
        current_node.match = s

    def _match_damreau_levhenstein(self, s, idx, current_dist, max_dist=None):
        # type: (str, int, int, Optional[int]) -> Optional[SearchResult]
        best_match = None
        if max_dist is not None and current_dist > max_dist:
            return None
        if idx >= len(s):
            if self.match is not None:
                return SearchResult(self.match, current_dist)
        elif idx < len(s):
            # Try to match with a deletion in the string
            best_match = min_cost_match(
                best_match, self._match_damreau_levhenstein(s, idx + 1, current_dist + 1, max_dist)
            )
        for (c, tree) in self.branches.items():
            if idx < len(s):
                if c == s[idx]:
                    # Found a matching character
                    best_match = min_cost_match(
                        best_match, tree._match_damreau_levhenstein(s, idx + 1, current_dist, max_dist)
                    )
                else:
                    # Try to match with a substitution
                    best_match = min_cost_match(
                        best_match, tree._match_damreau_levhenstein(s, idx + 1, current_dist + 1, max_dist)
                    )
            if idx + 1 < len(s) and s[idx] in tree.branches and c == s[idx + 1]:
                best_match = min_cost_match(
                    best_match, tree.branches[s[idx]]._match_damreau_levhenstein(s, idx + 2, current_dist + 1, max_dist)
                )
            # Try to match with an addition in the trie
            best_match = min_cost_match(best_match, tree._match_damreau_levhenstein(s, idx, current_dist + 1, max_dist))
        return best_match

    def match_damreau_levhenstein(self, s, max_dist=None):
        # type: (str, Optional[int]) -> Optional[SearchResult]
        return self._match_damreau_levhenstein(s, 0, 0, max_dist)


class FuzzyEnvMatcher(object):
    SCANNED_PREFIX = "DD_"

    def __init__(self):
        self.matcher = Trie()
        for env_name in os.environ.keys():
            if env_name.startswith(self.SCANNED_PREFIX):
                self.matcher.insert(env_name)
        self.seen = set()

    def get(self, key, default=None):
        # type: (str, Optional[str]) -> Optional[str]
        if key in os.environ:
            return os.environ.get(key, default)
        if key.startswith(self.SCANNED_PREFIX) and key not in self.seen:
            self.seen.add(key)
            # We bound the number errors we look for between min 1 error and max 2 errors
            max_dist = min(max((len(key) - len(self.SCANNED_PREFIX)) / 3, 1), 2)
            match = self.matcher.match_damreau_levhenstein(key, max_dist)
            if match is not None:
                log.warning("Env variable %s not recognized, did you mean %s", key, match.match)
        return default


env = FuzzyEnvMatcher()


def getenv(key, default=None):
    return env.get(key, default=default)
