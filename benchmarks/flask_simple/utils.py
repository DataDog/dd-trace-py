import bm.flask_utils as flask_utils
import bm.utils as utils
import requests


def _post_response():
    HEADERS = {
        "SERVER_PORT": "8000",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_TYPE": "application/json",
        "HTTP_HOST": "localhost:8000",
        "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "HTTP_SEC_FETCH_DEST": "document",
        "HTTP_ACCEPT_ENCODING": "gzip, deflate, br",
        "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9",
        "User-Agent": "dd-test-scanner-log",
    }
    r = requests.post(flask_utils.SERVER_URL + "post-view", data=utils.EXAMPLE_POST_DATA, headers=HEADERS)
    r.raise_for_status()
