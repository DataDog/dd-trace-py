import abc
import dataclasses
from typing import List, Any, Optional


@dataclasses.dataclass(frozen=True)
class _CIVisibilityItemIdBase:
    pass

class _CIVisibilityAPIBase(abc.ABC):
    def __init__(self):
        raise NotImplementedError("This class is not meant to be instantiated")



    @staticmethod
    @abc.abstractmethod
    def register(item_id: Optional[_CIVisibilityItemIdBase], *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def start(item_id: Optional[_CIVisibilityItemIdBase], *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def finish(item_id: Optional[_CIVisibilityItemIdBase], force_finish_children: bool = False, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def get_tag(item_id: Optional[_CIVisibilityItemIdBase], tag_name: str):
        pass

    @staticmethod
    @abc.abstractmethod
    def get_tags(item_id: Optional[_CIVisibilityItemIdBase], tag_names: List[str]):
        pass

    @staticmethod
    @abc.abstractmethod
    def set_tag(item_id: Optional[_CIVisibilityItemIdBase], tag_name: str, tag_value: Any):
        pass

    @staticmethod
    @abc.abstractmethod
    def set_tags(item_id: Optional[_CIVisibilityItemIdBase], tag_name: str, tag_value: Any, *args, **kwargs):
        pass

    @staticmethod
    @abc.abstractmethod
    def delete_tag(item_id: Optional[_CIVisibilityItemIdBase], tag_name: str):
        pass

    @staticmethod
    @abc.abstractmethod
    def delete_tags(item_id: Optional[_CIVisibilityItemIdBase], tag_names: List[str]):
        pass

