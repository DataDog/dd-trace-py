import abc


class CIVisibilityBase(abc.ABC):

    def __init__(self):
        self.span = None
    @abc.abstractmethod
    def start(self):
        pass

    @abc.abstractmethod
    def finish(self):
        pass

    @abc.abstractmethod
    def set_tag(self):
        pass

    @abc.abstractmethod
    def set_tags(self):
        pass

    @abc.abstractmethod
    def get_tag(self):
        pass

    @abc.abstractmethod
    def delete_tag(self, tag_name: str):
        pass

    @abc.abstractmethod
    def delete_tags(self, tag_names: List[str]):
        pass