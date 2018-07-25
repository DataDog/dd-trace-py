
def get_wsgi_header(header):
    return "HTTP_{}".format(header.upper().replace("-", "_"))
