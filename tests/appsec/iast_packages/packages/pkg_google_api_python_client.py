"""
google-api-python-client==2.111.0
https://pypi.org/project/google-api-core/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]

# The ID of a sample document.
DOCUMENT_ID = "test1234"

pkg_google_api_python_client = Blueprint("pkg_google_api_python_client", __name__)


@pkg_google_api_python_client.route("/google-api-python-client")
def pkg_google_view():
    response = ResultResponse(request.args.get("package_param"))
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    """Shows basic usage of the Docs API.
    Prints the title of a sample document.
    """

    class FakeResponse:
        status = 200

    class FakeHttp:
        def request(self, *args, **kwargs):
            return FakeResponse(), '{"a": "1"}'

    class FakeCredentials:
        def to_json(self):
            return "{}"

        def authorize(self, *args, **kwargs):
            return FakeHttp()

    creds = FakeCredentials()
    try:
        service = build("docs", "v1", credentials=creds)
        # Retrieve the documents contents from the Docs service.
        document = service.documents().get(documentId=DOCUMENT_ID).execute()
        _ = f"The title of the document is: {document.get('title')}"
    except HttpError:
        pass
    return response.json()
