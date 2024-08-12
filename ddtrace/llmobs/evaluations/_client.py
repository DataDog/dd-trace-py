from typing import Dict


try:
    import requests
except Exception:
    requests = None


from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
unset = None


class LLMObsSpansAPI:
    def __init__(self):
        self.endpoint = "http://127.0.0.1:8080/api/unstable/llm-obs/v1/spans/events/search"
        self.headers = {
            "dd-api-key": "dev",
        }

    def search_spans(self, filter_query, filter_from=unset, filter_to=unset, page_cursor=unset, page_limit=unset):
        kwargs: Dict[str, any] = {}

        if filter_query is not unset:
            kwargs["filter_query"] = filter_query

        if filter_from is not unset:
            kwargs["filter_from"] = filter_from

        if filter_to is not unset:
            kwargs["filter_to"] = filter_to

        if page_cursor is not unset:
            kwargs["page_cursor"] = page_cursor

        if page_limit is not unset:
            kwargs["page_limit"] = page_limit

        json_body = {
            "data": {
                "type": "spans",
                "attributes": {
                    "filter": {
                        "query": kwargs["filter_query"],
                        "from": kwargs["filter_from"],
                        "to": kwargs["filter_to"],
                    }
                },
            }
        }

        resp = requests.post(self.endpoint, headers=self.headers, json=json_body)
        body = resp.json()

        yield body.get("data").get("attributes").get("spans")

        hasNextPage = True
        while hasNextPage and body.get("links"):
            next_url = body.get("links").get("next")
            if next_url is not None:
                resp = requests.get(next_url, headers=self.headers)
                body = resp.json()
                yield body.get("data").get("attributes").get("spans")
            else:
                hasNextPage = False
