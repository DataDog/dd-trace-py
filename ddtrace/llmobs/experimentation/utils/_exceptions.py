from typing import Optional

class DatasetFileError(Exception):
    """
    Exception raised when there are errors reading or processing dataset files,
    such as CSV or JSON errors or file permission issues.
    """

    pass


class ExperimentTaskError(Exception):
    """
    Exception raised when a task fails during experiment execution.

    Attributes:
        message (str): Error message describing the failure.
        row_idx (int): The index of the dataset record on which the task failed.
        original_error (Exception): The original error object (if any).
    """

    def __init__(self, message: str, row_idx: int, original_error: Optional[Exception] = None) -> None:
        self.row_idx = row_idx
        self.original_error = original_error
        super().__init__(message)

