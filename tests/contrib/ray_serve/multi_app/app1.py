from ray import serve
from starlette.requests import Request


@serve.deployment
class HelloApp:
    async def __call__(self, request: Request):
        return {"message": "Hello from app1"}


app = HelloApp.bind()
