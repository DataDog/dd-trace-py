import asyncio
from uuid import uuid4

from temporalio.client import Client

from tests.contrib.temporalio.sample_app.workflows import GreetingWorkflow


TASK_QUEUE = "datadog-temporal-sample"


async def run(client: Client, workflow_id: str) -> tuple[str, str]:
    handle = await client.start_workflow(
        GreetingWorkflow.run,
        "Temporal",
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    initial_salutation = await handle.query(GreetingWorkflow.current_salutation)
    await handle.signal(GreetingWorkflow.approve, "Welcome")
    return initial_salutation, await handle.result()


async def main() -> None:
    client = await Client.connect("localhost:7233")
    _, greeting = await run(client, f"datadog-temporal-sample-{uuid4()}")
    print(greeting)


if __name__ == "__main__":
    asyncio.run(main())
