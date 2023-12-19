"""
python-dateutil==2.8.2

https://pypi.org/project/python-dateutil/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_python_dateutil = Blueprint("package_python-dateutil", __name__)


@pkg_python_dateutil.route("/python-dateutil")
def pkg_idna_view():
    from dateutil.easter import *  # noqa
    from dateutil.parser import *  # noqa
    from dateutil.relativedelta import *  # noqa
    from dateutil.rrule import *  # noqa

    response = ResultResponse(request.args.get("package_param"))  # noqa
    response.result1 = parse(response.package_param)  # noqa
    today = response.result1.date()
    year = rrule(YEARLY, dtstart=response.result1, bymonth=8, bymonthday=13, byweekday=FR)[0].year  # noqa
    rdelta = relativedelta(easter(year), today)  # noqa
    response.result2 = "And the Easter of that year is: %s" % (today + rdelta)
    return response.json()
