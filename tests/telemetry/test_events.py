# from ddtrace.internal.telemetry.data import create_integration
# from ddtrace.internal.telemetry.events import AppClosedPayload
# from ddtrace.internal.telemetry.events import AppIntegrationsChangedPayload
# from ddtrace.internal.telemetry.events import AppStartedPayload


# def test_app_started_payload_to_dict():
#     """validates the return value of AppStartedPayload.to_dict"""
#     asp = AppStartedPayload()
#     assert asp == {"dependencies": asp.dependencies, "configurations": {}}


# def test_app_closed_payload_to_dict():
#     """validates the return value of AppClosedPayload.to_dict"""
#     assert AppClosedPayload() == {}


# def test_app_integrations_changed_to_dict():
#     """validates the return value of AppIntegrationsChangedPayload.to_dict"""
#     integrations = [
#         create_integration("integration-1", "0.0.0", False, False, "no", "error"),
#     ]
#     payload = AppIntegrationsChangedPayload(integrations)

#     assert len(payload.integrations) == 1
#     assert payload == {
#         "integrations": [
#             {
#                 "name": "integration-1",
#                 "version": "0.0.0",
#                 "enabled": False,
#                 "auto_enabled": False,
#                 "compatible": "no",
#                 "error": "error",
#             },
#         ],
#     }


# def test_app_started_payload_dependencies():
#     """
#     validates that dependencies are set after an AppStartedPayload object
#     is created
#     """
#     payload = AppStartedPayload()

#     assert len(payload.dependencies) > 0

#     for dep in payload.dependencies:
#         assert "name" in dep
#         assert "version" in dep

#         assert dep["name"]
#         assert dep["version"]


# def test_integrations_changed_payload_integrations():
#     """
#     validates that integrations are set after an AppIntegrationsChangedPayload
#     object is created
#     """
#     integrations = [
#         create_integration("integration-1"),
#         create_integration("integration-2"),
#     ]
#     payload = AppIntegrationsChangedPayload(integrations)

#     assert len(payload.integrations) == 2

#     assert "name" in payload.integrations[0]
#     assert payload.integrations[0]["name"] == "integration-1"

#     assert "name" in payload.integrations[1]
#     assert payload.integrations[1]["name"] == "integration-2"
