import abc
from typing import Any, Dict, List

from ddtrace.internal.ci_visibility.recorder import CIVisibility
class CIVisibilityError(Exception):
    pass

def _requires_civisibility_enabled(_ = None):

    def wrapper(func, *args, **kwargs):
        if not CIVisibility.enabled:
            raise CIVisibilityError("CI Visibility is not enabled")
        return func(*args, **kwargs)

    return wrapper

class CIVisibilityItemBase(abc.ABC):

    @abc.abstractmethod
    def start(self):
        pass

    @abc.abstractmethod
    def finish(self):
        pass

    @abc.abstractmethod
    def set_tag(self, tag_name: str, tag_value: Any) -> None:
        pass

    @abc.abstractmethod
    def set_tags(self, tags: Dict[str, Any]) -> None:
        pass

    @abc.abstractmethod
    def get_tag(self) -> Any:
        pass

    @abc.abstractmethod
    def get_tags(self, tags: List[str]) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    def delete_tag(self, tag_name: str) -> None:
        pass

    @abc.abstractmethod
    def delete_tags(self, tag_names: List[str]) -> None:
        pass