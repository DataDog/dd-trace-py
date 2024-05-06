from collections import defaultdict
import json
from pathlib import Path
from typing import Dict
from typing import List
from typing import Tuple


try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    # Compatibility with Python 3.7
    from typing_extensions import TypedDict


class CoverageFilePayload(TypedDict):
    filename: str
    segments: List[Tuple[int, int, int, int, int]]


class CICoverageSegment:
    """Container for coverage segment data

    Columns and counts are currently unused in the current data model in ddtrace.internal.ci_visibility.coverage and
    .encoder , so they are not included in this class.
    """

    def __init__(self, start_line: int, end_line: int):
        self.start_line: int = start_line
        self.end_line: int = end_line


class CICoverageData:
    """Container for coverage data for an item (suite or test)"""

    def __init__(self) -> None:
        self._coverage_data: Dict[Path, List[CICoverageSegment]] = defaultdict(list)

    def __bool__(self):
        return bool(self._coverage_data)

    def add_coverage_segment(self, file_path: Path, start_line, end_line):
        """Add a coverage segment to the coverage data"""
        self._coverage_data[file_path.absolute()].append(CICoverageSegment(start_line, end_line))

    def add_coverage_segments(self, segments: Dict[Path, List[Tuple[int, int]]]):
        """Add coverage segments to the coverage data"""
        for file_path, segment_data in segments.items():
            for segment in segment_data:
                self.add_coverage_segment(file_path, segment[0], segment[1])

    def _build_payload(self, root_dir: Path) -> List[CoverageFilePayload]:
        """Generate a CI Visibility coverage payload

        Tuples are used here since JSON serializes tuples as lists.
        """
        coverage_data = []
        for file_path, segments in self._coverage_data.items():
            try:
                # Report relative path unless the file path is not relative to root_dir
                # Paths are assumed to be absolute based on having been converted at instantiation / add time.
                relative_path = file_path.relative_to(root_dir)
            except ValueError:
                relative_path = file_path
            segments_data = []
            for segment in segments:
                segments_data.append((segment.start_line, 0, segment.end_line, 0, -1))
            file_payload: CoverageFilePayload = {"filename": str(relative_path), "segments": segments_data}
            coverage_data.append(file_payload)
        return coverage_data

    def build_payload(self, root_dir: Path) -> str:
        """Generate a CI Visibility coverage payload in JSON format"""
        return json.dumps({"files": self._build_payload(root_dir)})
