import base64
import json

from ddtrace.internal.datastreams.botocore import get_datastreams_context


class TestGetDatastreamsContext:
    def test_sqs_to_lambda_format_with_datadog_context(self):
        """Test SQS -> Lambda format with _datadog messageAttributes."""
        trace_context = {
            "x-datadog-trace-id": "123456789",
            "x-datadog-parent-id": "987654321",
            "x-datadog-sampling-priority": "1",
        }

        lambda_record = {
            "messageId": "059f36b4-87a3-44ab-83d2-661975830a7d",
            "receiptHandle": "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
            "body": "Test message.",
            "attributes": {
                "ApproximateReceiveCount": "1",
                "SentTimestamp": "1545082649183",
                "SenderId": "AIDAIENQZJOLO23YVJ4VO",
                "ApproximateFirstReceiveTimestamp": "1545082649185",
            },
            "messageAttributes": {
                "_datadog": {
                    "stringValue": json.dumps(trace_context),
                    "stringListValues": [],
                    "binaryListValues": [],
                    "dataType": "String",
                },
                "myAttribute": {
                    "stringValue": "myValue",
                    "stringListValues": [],
                    "binaryListValues": [],
                    "dataType": "String",
                },
            },
            "md5OfBody": "e4e68fb7bd0e697a0ae8f1bb342846b3",
            "eventSource": "aws:sqs",
            "eventSourceARN": "arn:aws:sqs:us-east-2:123456789012:my-queue",
            "awsRegion": "us-east-2",
        }

        result = get_datastreams_context(lambda_record)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "123456789"
        assert result["x-datadog-parent-id"] == "987654321"
        assert result["x-datadog-sampling-priority"] == "1"

    def test_sqs_json_body_reads_top_level_message_attributes(self):
        """SQS message with a JSON body resolves _datadog from the top-level attributes."""
        trace_context = {"dd-pathway-ctx-base64": "leP46s4u2yD00vLN0Gf00vLN0Gc="}

        message = {
            "Body": json.dumps({"tax_document_id": "5b51a91c", "process_type": "STANDARD_SOURCE", "job_type": "TAX"}),
            "MessageAttributes": {"_datadog": {"StringValue": json.dumps(trace_context), "DataType": "String"}},
        }

        assert get_datastreams_context(message) == trace_context

    def test_sqs_plain_body_reads_top_level_message_attributes(self):
        """SQS message with a non-JSON body resolves the context."""
        trace_context = {"dd-pathway-ctx-base64": "leP46s4u2yD00vLN0Gf00vLN0Gc="}

        message = {
            "Body": "Message No.0",
            "MessageAttributes": {"_datadog": {"StringValue": json.dumps(trace_context), "DataType": "String"}},
        }

        assert get_datastreams_context(message) == trace_context

    def test_sns_to_sqs_notification_reads_body_message_attributes(self):
        """SNS -> SQS (non-raw): the context lives in the JSON body's MessageAttributes."""
        trace_context = {"dd-pathway-ctx-base64": "leP46s4u2yD00vLN0Gf00vLN0Gc="}

        message = {
            "Body": json.dumps(
                {
                    "Type": "Notification",
                    "Message": "hello",
                    "MessageAttributes": {
                        "_datadog": {
                            "Type": "Binary",
                            "Value": base64.b64encode(json.dumps(trace_context).encode()).decode(),
                        }
                    },
                }
            )
        }

        assert get_datastreams_context(message) == trace_context

    def test_sns_to_sqs_notification_falls_back_when_top_level_attributes_lack_datadog(self):
        """Top-level attributes present but without _datadog still falls back to the body."""
        trace_context = {"dd-pathway-ctx-base64": "leP46s4u2yD00vLN0Gf00vLN0Gc="}

        message = {
            "MessageAttributes": {"other": {"StringValue": "value", "DataType": "String"}},
            "Body": json.dumps(
                {
                    "Type": "Notification",
                    "MessageAttributes": {
                        "_datadog": {
                            "Type": "Binary",
                            "Value": base64.b64encode(json.dumps(trace_context).encode()).decode(),
                        }
                    },
                }
            ),
        }

        assert get_datastreams_context(message) == trace_context

    def test_sns_to_sqs_raw_delivery_reads_top_level_binary_value(self):
        """SNS -> SQS raw delivery carries _datadog as a top-level BinaryValue."""
        trace_context = {"dd-pathway-ctx-base64": "leP46s4u2yD00vLN0Gf00vLN0Gc="}

        message = {
            "Body": "raw delivered body",
            "MessageAttributes": {
                "_datadog": {"BinaryValue": json.dumps(trace_context).encode(), "DataType": "Binary"}
            },
        }

        assert get_datastreams_context(message) == trace_context

    def test_no_datadog_attribute_returns_none(self):
        message = {
            "Body": json.dumps({"job_type": "TAX"}),
            "MessageAttributes": {"other": {"StringValue": "value", "DataType": "String"}},
        }

        assert get_datastreams_context(message) is None

    def test_no_message_attributes_returns_none(self):
        assert get_datastreams_context({"Body": json.dumps({"job_type": "TAX"})}) is None
