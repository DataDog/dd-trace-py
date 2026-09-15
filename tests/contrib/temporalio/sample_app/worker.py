import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from tests.contrib.temporalio.sample_app.activities import compose_greeting
from tests.contrib.temporalio.sample_app.starter import TASK_QUEUE
from tests.contrib.temporalio.sample_app.workflows import GreetingWorkflow


async def main() -> None:
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GreetingWorkflow],
        activities=[compose_greeting],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
