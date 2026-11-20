from typing import Any

# Other native exports are not typed yet.
def __getattr__(name: str) -> Any: ...
