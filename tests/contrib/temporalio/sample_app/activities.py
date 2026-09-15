from temporalio import activity


@activity.defn
async def compose_greeting(name: str, salutation: str) -> str:
    return f"{salutation}, {name}!"
