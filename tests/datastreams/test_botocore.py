import json


from ddtrace.internal.datastreams.botocore import get_datastreams_context


class TestGetDatastreamsContext:
    """Test the get_datastreams_context function with various message formats."""

    def test_sqs_to_lambda_format_with_datadog_context(self):
        """Test SQS -> Lambda format with _datadog messageAttributes."""
        # Sample trace context data
        trace_context = {
            "x-datadog-trace-id": "123456789",
            "x-datadog-parent-id": "987654321",
            "x-datadog-sampling-priority": "1"
        }

        # Lambda event record with SQS message format
        lambda_record = {
            "messageId": "059f36b4-87a3-44ab-83d2-661975830a7d",
            "receiptHandle": "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
            "body": "Test message.",
            "attributes": {
                "ApproximateReceiveCount": "1",
                "SentTimestamp": "1545082649183",
                "SenderId": "AIDAIENQZJOLO23YVJ4VO",
                "ApproximateFirstReceiveTimestamp": "1545082649185"
            },
            "messageAttributes": {
                "_datadog": {
                    "stringValue": json.dumps(trace_context),
                    "stringListValues": [],
                    "binaryListValues": [],
                    "dataType": "String"
                },
                "myAttribute": {
                    "stringValue": "myValue",
                    "stringListValues": [],
                    "binaryListValues": [],
                    "dataType": "String"
                }
            },
            "md5OfBody": "e4e68fb7bd0e697a0ae8f1bb342846b3",
            "eventSource": "aws:sqs",
            "eventSourceARN": "arn:aws:sqs:us-east-2:123456789012:my-queue",
            "awsRegion": "us-east-2"
        }

        # Test the function
        result = get_datastreams_context(lambda_record)

        # Assertions
        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "123456789"
        assert result["x-datadog-parent-id"] == "987654321"
        assert result["x-datadog-sampling-priority"] == "1"
