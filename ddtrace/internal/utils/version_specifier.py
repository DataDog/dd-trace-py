from typing import List
from typing import Tuple


class VersionSpecifier:
    """A class to handle version specifiers and version comparison.

    Supports operators: >, <, >=, <=
    Supports version ranges like: >1,<3
    Supports * wildcard to indicate any version is supported
    """

    def __init__(self, spec: str):
        """Initialize with a version specifier string.

        Args:
            spec: Version specifier string (e.g. ">1,<3" or ">=2.0.0" or "*")
        """
        self.specs = self._parse_spec(spec)

    def _parse_spec(self, spec: str) -> List[Tuple[str, str]]:
        """Parse a version specifier string into a list of (operator, version) tuples.

        Args:
            spec: Version specifier string

        Returns:
            List of (operator, version) tuples
        """
        if spec.strip() == "*":
            return [("*", "*")]

        specs = []
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue

            # Find the operator
            op = ""
            if part.startswith(">="):
                op = ">="
                version = part[2:]
            elif part.startswith("<="):
                op = "<="
                version = part[2:]
            elif part.startswith(">"):
                op = ">"
                version = part[1:]
            elif part.startswith("<"):
                op = "<"
                version = part[1:]
            else:
                # No operator means exact version
                op = "=="
                version = part

            specs.append((op, version.strip()))
        return specs

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two version strings.

        Args:
            v1: First version string
            v2: Second version string

        Returns:
            -1 if v1 < v2
            0 if v1 == v2
            1 if v1 > v2
        """
        v1_parts = [int(x) for x in v1.split(".")[:3]]
        v2_parts = [int(x) for x in v2.split(".")[:3]]

        # Pad with zeros to make lengths equal
        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))

        for i in range(max_len):
            if v1_parts[i] < v2_parts[i]:
                return -1
            if v1_parts[i] > v2_parts[i]:
                return 1
        return 0

    def contains(self, version: str) -> bool:
        """Check if a version satisfies all the specifiers.

        Args:
            version: Version string to check

        Returns:
            True if the version satisfies all specifiers, False otherwise
        """
        if self.specs and self.specs[0] == ("*", "*"):
            return True

        for op, spec_version in self.specs:
            cmp = self._compare_versions(version, spec_version)

            if op == ">=" and cmp < 0:
                return False
            elif op == "<=" and cmp > 0:
                return False
            elif op == ">" and cmp <= 0:
                return False
            elif op == "<" and cmp >= 0:
                return False
            elif op == "==" and cmp != 0:
                return False

        return True
