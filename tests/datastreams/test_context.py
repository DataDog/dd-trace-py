import json

from ddtrace.internal.datastreams.botocore import get_datastreams_context


def test_lowercase_message_attributes():
    """Test case-insensitivity for 'messageAttributes' key"""
    test_data = {"test_key": "test_value"}
    message = {
        "messageAttributes": {  # lowercase instead of MessageAttributes
            "_datadog": {
                "StringValue": json.dumps(test_data),
            }
        }
    }

    context = get_datastreams_context(message)
    assert context == test_data


def test_lowercase_string_value():
    """Test case-insensitivity for 'stringValue' key"""
    test_data = {"test_key": "test_value"}
    message = {
        "MessageAttributes": {
            "_datadog": {
                "stringValue": json.dumps(test_data),  # lowercase instead of StringValue
            }
        }
    }

    context = get_datastreams_context(message)
    assert context == test_data
