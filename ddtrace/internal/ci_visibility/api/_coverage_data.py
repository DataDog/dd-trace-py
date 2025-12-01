from collections import defaultdict
from pathlib import Path
from typing import Dict
from typing import List
from typing import TypedDict  # noqa:F401
from typing import Union

from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


class CoverageFilePayload(TypedDict):
    filename: str
    bitmap: bytes


class TestVisibilityCoverageData:
    """Container for coverage data for an item (suite or test)"""

    __test__ = False

    def __init__(self) -> None:
        self._coverage_data: Dict[Path, CoverageLines] = defaultdict(CoverageLines)

    def __bool__(self):
        return bool(self._coverage_data)

    def get_data(self) -> Dict[Path, CoverageLines]:
        return self._coverage_data

    def add_covered_files(self, covered_files: Union[Dict[Path, CoverageLines], Dict[str, CoverageLines]]):
        """
        Add coverage segments to the coverage data.
        
        Args:
            covered_files: Dict mapping file paths (Path or str) to CoverageLines
        """
        for file_path, covered_lines in covered_files.items():
            # Convert string paths to Path objects if needed
            path = file_path if isinstance(file_path, Path) else Path(file_path)
            self._coverage_data[path.absolute()].update(covered_lines)

    def _build_payload(self, root_dir: Path) -> List[CoverageFilePayload]:
        """Generate a Test Visibility coverage payload"""
        coverage_data = []
        for file_path, covered_lines in self._coverage_data.items():
            try:
                # Report relative path unless the file path is not relative to root_dir
                # Paths are assumed to be absolute based on having been converted at instantiation / add time.
                relative_path = file_path.relative_to(root_dir)
            except ValueError:
                relative_path = file_path
            path_str = f"/{str(relative_path)}"
            file_payload: CoverageFilePayload = {"filename": path_str, "bitmap": covered_lines.to_bytes()}
            coverage_data.append(file_payload)
        return coverage_data

    def build_payload(self, root_dir: Path) -> Dict[str, List[CoverageFilePayload]]:
        return {"files": self._build_payload(root_dir)}
