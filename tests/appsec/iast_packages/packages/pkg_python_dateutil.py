"""
python-dateutil==2.8.2

https://pypi.org/project/python-dateutil/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_python_dateutil = Blueprint("package_python-dateutil", __name__)


@pkg_python_dateutil.route("/python-dateutil")
def pkg_dateutil_view():
    from dateutil.easter import easter
    from dateutil.parser import parse
    from dateutil.relativedelta import relativedelta
    from dateutil.rrule import FR
    from dateutil.rrule import YEARLY
    from dateutil.rrule import rrule

    response = ResultResponse(request.args.get("package_param"))
    response.result1 = parse(response.package_param)
    today = response.result1.date()
    year = rrule(YEARLY, dtstart=response.result1, bymonth=8, bymonthday=13, byweekday=FR)[0].year
    rdelta = relativedelta(easter(year), today)
    response.result2 = "And the Easter of that year is: %s" % (today + rdelta)
    return response.json()
