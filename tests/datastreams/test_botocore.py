import base64
import json

import pytest

from ddtrace.internal.datastreams.botocore import get_datastreams_context


TRACE_CONTEXT = {
    "x-datadog-trace-id": "123456789",
    "x-datadog-parent-id": "987654321",
    "x-datadog-sampling-priority": "1",
}


class TestGetDatastreamsContext:
    def test_sqs_to_lambda_format_with_datadog_context(self):
        """Test SQS -> Lambda format with _datadog messageAttributes."""
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
                    "stringValue": json.dumps(TRACE_CONTEXT),
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
        assert result == TRACE_CONTEXT

    def test_plain_sqs_with_json_body_reads_top_level_message_attributes(self):
        """Plain SQS message with a JSON body must use top-level MessageAttributes.

        Regression test for DSMS-149: the old code overwrote the attributes source
        with the parsed Body, causing MessageAttributes to be read from the JSON
        payload instead of from the SQS message envelope.
        """
        sqs_message = {
            "MessageId": "abc123",
            "ReceiptHandle": "handle...",
            "Body": json.dumps({"order_id": 42, "status": "placed"}),
            "MessageAttributes": {
                "_datadog": {
                    "StringValue": json.dumps(TRACE_CONTEXT),
                    "DataType": "String",
                }
            },
        }

        result = get_datastreams_context(sqs_message)

        assert result == TRACE_CONTEXT

    def test_plain_sqs_with_non_json_body_reads_top_level_message_attributes(self):
        """Plain SQS message with a plain-text body still finds top-level attributes."""
        sqs_message = {
            "MessageId": "abc124",
            "ReceiptHandle": "handle...",
            "Body": "plain text body",
            "MessageAttributes": {
                "_datadog": {
                    "StringValue": json.dumps(TRACE_CONTEXT),
                    "DataType": "String",
                }
            },
        }

        result = get_datastreams_context(sqs_message)

        assert result == TRACE_CONTEXT

    def test_sns_wrapped_sqs_reads_attributes_from_notification_envelope(self):
        """SNS -> SQS: MessageAttributes must come from the SNS notification Body."""
        sns_notification = {
            "Type": "Notification",
            "MessageId": "sns-msg-id",
            "Message": "Hello from SNS",
            "MessageAttributes": {
                "_datadog": {
                    "Type": "Binary",
                    "Value": base64.b64encode(json.dumps(TRACE_CONTEXT).encode()).decode(),
                }
            },
        }
        sqs_message = {
            "MessageId": "sqs-msg-id",
            "ReceiptHandle": "handle...",
            "Body": json.dumps(sns_notification),
            "MessageAttributes": {},
        }

        result = get_datastreams_context(sqs_message)

        assert result == TRACE_CONTEXT

    def test_sqs_json_body_without_datadog_attribute_returns_none(self):
        """JSON body with no _datadog attribute at the top level returns None, not an error."""
        sqs_message = {
            "MessageId": "abc125",
            "ReceiptHandle": "handle...",
            "Body": json.dumps({"some": "payload"}),
            "MessageAttributes": {},
        }

        result = get_datastreams_context(sqs_message)

        assert result is None

    @pytest.mark.parametrize(
        "body",
        [
            json.dumps({"order_id": 1}),
            "plain text",
            "",
            None,
        ],
    )
    def test_sqs_missing_datadog_attribute_returns_none(self, body):
        """Messages with no _datadog MessageAttribute must return None regardless of body type."""
        sqs_message = {
            "MessageId": "abc126",
            "ReceiptHandle": "handle...",
            "Body": body,
            "MessageAttributes": {"other": {"StringValue": "x", "DataType": "String"}},
        }

        result = get_datastreams_context(sqs_message)

        assert result is None
