import azure.durable_functions as df
import azure.functions as func

import ddtrace.auto  # noqa: F401


app = df.DFApp()


@app.route(route="startactivity", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
@app.durable_client_input(client_name="client")
async def start_activity(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    instance_id = await client.start_new("activity_orchestrator")
    return await client.wait_for_completion_or_create_check_status_response(req, instance_id)


@app.route(route="startentity", auth_level=func.AuthLevel.ANONYMOUS, methods=[func.HttpMethod.GET])
@app.durable_client_input(client_name="client")
async def start_entity(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    instance_id = await client.start_new("entity_orchestrator")
    return await client.wait_for_completion_or_create_check_status_response(req, instance_id)


@app.orchestration_trigger(context_name="context")
def activity_orchestrator(context: df.DurableOrchestrationContext):
    result = yield context.call_activity("durable_activity", "Datadog")
    return result


@app.orchestration_trigger(context_name="context")
def entity_orchestrator(context: df.DurableOrchestrationContext):
    entity_id = df.EntityId("counter", "durable")
    result = yield context.call_entity(entity_id, "add", 1)
    return result


@app.activity_trigger(input_name="name")
def durable_activity(name: str) -> str:
    return f"Hello {name}"


@app.entity_trigger(context_name="context")
def counter(context: df.DurableEntityContext) -> None:
    state = context.get_state(lambda: 0)
    operation = context.operation_name

    if operation == "add":
        state += context.get_input() or 0
    elif operation == "reset":
        state = 0

    context.set_state(state)
    context.set_result(state)
