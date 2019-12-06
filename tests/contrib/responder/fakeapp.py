

import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logging.info("This is an info message")

from ddtrace import patch_all; patch_all()

import responder

def run():
    api = responder.API()

    @api.route("/home")
    def home(req, resp):
        resp.text = 'thunk'


    print("=" *50)
    print("making fake requests")
    print("=" *50)
    r = api.session().get("/home")
    print(r)
    import time; time.sleep(1)


if __name__ == '__main__':
    run()
