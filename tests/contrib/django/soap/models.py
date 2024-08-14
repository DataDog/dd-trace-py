from spyne import Boolean
from spyne import ComplexModel
from spyne import ComplexModelBase
from spyne import String
from spyne import Unicode
from spyne.util.odict import odict


class OrderedModel(object):
    """
    Ugly hack to create an ordered model in Spyne, but there's no other way.
    See: https://mail.python.org/pipermail/soap/2013-June/001113.html
    """

    def __init__(self):
        self.result = odict()

    def fields(self):
        """This method should be overwritten."""
        raise NotImplementedError("Overwrite the OrderedModel.fields() method.")

    def model_names(self):
        """This method should be overwritten."""
        raise NotImplementedError("Overwrite the OrderedModel.model_names() method.")

    def produce(self, type_name, prefix=""):
        """Produce the actual model."""
        for field in self.fields():
            if isinstance(field[1], OrderedModel):
                self.result[field[0]] = field[1].produce(field[2])
            else:
                self.result[field[0]] = field[1]
        return ComplexModelBase.produce(prefix, type_name, self.result)


class LeaveStatusModel(OrderedModel):
    def fields(self):
        return [
            ("LeaveID", String(), "leave_id"),
            ("Description", String(), "description"),
        ]

    def model_names(self):
        return ("leave", "LeaveStatus")


class ResponseModel(ComplexModel):
    __namespace__ = ""

    success = Boolean
    errorText = Unicode
