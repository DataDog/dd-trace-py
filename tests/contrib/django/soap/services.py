import logging

from django.views.decorators.csrf import csrf_exempt
from lxml import etree
from spyne import Application  # noqa
from spyne import ServiceBase  # noqa
from spyne import rpc  # noqa
from spyne.protocol.soap import Soap11  # noqa
from spyne.server.django import DjangoApplication  # noqa

from .models import LeaveStatusModel
from .models import ResponseModel


logger = logging.getLogger(__name__)


class LeaveStatusService(ServiceBase):
    @rpc(LeaveStatusModel().produce("leave_status", ""), _body_style="bare", _returns=ResponseModel)
    def EmployeeLeaveStatus(self, leave_status):
        in_body_doc = etree.tostring(self.in_body_doc)
        logger.info("Leave service called with: ", in_body_doc)
        logger.info("Parsed Leave Status: ", leave_status)
        return ResponseModel(success=True, errorText=in_body_doc)


leave_status_app = Application(
    [
        LeaveStatusService,
    ],
    tns="http://kabisa.nl/soap/reproduction",
    in_protocol=Soap11(validator="lxml"),
    out_protocol=Soap11(),
)

leave_status_service = csrf_exempt(DjangoApplication(leave_status_app))
