"""
Standard http tags.

For example:

span.set_tag(URL, "/user/home")
span.set_tag(STATUS_CODE, 404)
"""

# type of the spans
TYPE = "http"

# tags
URL = "http.url"
METHOD = "http.method"
STATUS_CODE = "http.status_code"

# template render span type
TEMPLATE = 'template'

