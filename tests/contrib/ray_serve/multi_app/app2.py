from ray import serve
from starlette.requests import Request


@serve.deployment
class GoodbyeApp:
    async def __call__(self, request: Request):
        return {"message": "Goodbye from app2"}


app = GoodbyeApp.bind()
