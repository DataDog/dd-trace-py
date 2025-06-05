import base64
import json

from ddtrace.internal.datastreams.botocore import get_datastreams_context


class TestGetDatastreamsContext:
    def test_sqs_body_message_attributes_format(self):
        """Test format: message.Body.MessageAttributes._datadog.Value.decode() (SQS)"""
        trace_context = {
            "x-datadog-trace-id": "123456789",
            "x-datadog-parent-id": "987654321",
            "dd-pathway-ctx": "test-pathway-ctx",
        }

        binary_data = base64.b64encode(json.dumps(trace_context).encode("utf-8")).decode("utf-8")
        message_body = {
            "Type": "Notification",
            "MessageAttributes": {
                "_datadog": {
                    "Type": "Binary",
                    "Value": binary_data,
                }
            },
        }

        sqs_message = {
            "MessageId": "sqs-message-id",
            "Body": json.dumps(message_body),
            "ReceiptHandle": "test-receipt-handle",
        }

        result = get_datastreams_context(sqs_message)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "123456789"
        assert result["x-datadog-parent-id"] == "987654321"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    def test_sns_to_sqs_string_value_format(self):
        """Test format: message.MessageAttributes._datadog.StringValue (SNS -> SQS)"""
        trace_context = {
            "x-datadog-trace-id": "555444333",
            "x-datadog-parent-id": "111222333",
            "dd-pathway-ctx": "test-pathway-ctx",
        }

        sqs_message = {
            "MessageId": "12345678-1234-1234-1234-123456789012",
            "ReceiptHandle": "AQEB...",
            "Body": "Hello from SQS!",
            "Attributes": {"SentTimestamp": "1673001234567"},
            "MessageAttributes": {
                "_datadog": {"StringValue": json.dumps(trace_context), "DataType": "String"},
                "CustomAttribute": {"StringValue": "custom-value", "DataType": "String"},
            },
        }

        result = get_datastreams_context(sqs_message)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "555444333"
        assert result["x-datadog-parent-id"] == "111222333"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    def test_sns_to_sqs_binary_value_format(self):
        """Test format: message.MessageAttributes._datadog.BinaryValue.decode() (SNS -> SQS, raw)"""
        trace_context = {
            "x-datadog-trace-id": "333222111",
            "x-datadog-parent-id": "666555444",
            "dd-pathway-ctx": "test-pathway-ctx",
        }

        sqs_message = {
            "MessageId": "binary-message-id",
            "ReceiptHandle": "binary-receipt-handle",
            "Body": "Binary message content",
            "MessageAttributes": {
                "_datadog": {"BinaryValue": json.dumps(trace_context).encode("utf-8"), "DataType": "Binary"}
            },
        }

        result = get_datastreams_context(sqs_message)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "333222111"
        assert result["x-datadog-parent-id"] == "666555444"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    def test_sqs_to_lambda_string_value_format(self):
        """Test format: message.messageAttributes._datadog.stringValue (SQS -> lambda)"""
        trace_context = {
            "x-datadog-trace-id": "789123456",
            "x-datadog-parent-id": "321987654",
            "dd-pathway-ctx": "test-pathway-ctx",
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
        assert result["x-datadog-trace-id"] == "789123456"
        assert result["x-datadog-parent-id"] == "321987654"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    def test_sns_to_lambda_format(self):
        """Test format: message.Sns.MessageAttributes._datadog.Value.decode() (SNS -> lambda)"""
        trace_context = {
            "x-datadog-trace-id": "111111111",
            "x-datadog-parent-id": "222222222",
            "dd-pathway-ctx": "test-pathway-ctx",
        }
        binary_data = base64.b64encode(json.dumps(trace_context).encode("utf-8")).decode("utf-8")

        sns_lambda_record = {
            "EventSource": "aws:sns",
            "EventSubscriptionArn": "arn:aws:sns:us-east-1:123456789012:sns-topic:12345678-1234-1234-1234-123456789012",
            "Sns": {
                "Type": "Notification",
                "MessageId": "95df01b4-ee98-5cb9-9903-4c221d41eb5e",
                "TopicArn": "arn:aws:sns:us-east-1:123456789012:sns-topic",
                "Subject": "Test Subject",
                "Message": "Hello from SNS!",
                "Timestamp": "2023-01-01T12:00:00.000Z",
                "MessageAttributes": {"_datadog": {"Type": "Binary", "Value": binary_data}},
            },
        }

        result = get_datastreams_context(sns_lambda_record)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "111111111"
        assert result["x-datadog-parent-id"] == "222222222"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    def test_sns_to_sqs_to_lambda_binary_value_format(self):
        """Test format: message.messageAttributes._datadog.binaryValue.decode() (SNS -> SQS -> lambda, raw)"""
        trace_context = {
            "x-datadog-trace-id": "777666555",
            "x-datadog-parent-id": "444333222",
            "dd-pathway-ctx": "test-pathway-ctx",
        }
        binary_data = base64.b64encode(json.dumps(trace_context).encode("utf-8")).decode("utf-8")

        lambda_record = {
            "messageId": "test-message-id",
            "receiptHandle": "test-receipt-handle",
            "body": "Test message body",
            "messageAttributes": {"_datadog": {"binaryValue": binary_data, "dataType": "Binary"}},
            "eventSource": "aws:sqs",
            "eventSourceARN": "arn:aws:sqs:us-west-2:123456789012:test-queue",
        }

        result = get_datastreams_context(lambda_record)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "777666555"
        assert result["x-datadog-parent-id"] == "444333222"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    def test_sns_to_sqs_to_lambda_body_format(self):
        """Test format: message.body.MessageAttributes._datadog.Value.decode() (SNS -> SQS -> lambda)"""
        trace_context = {
            "x-datadog-trace-id": "123987456",
            "x-datadog-parent-id": "654321987",
            "x-datadog-sampling-priority": "1",
            "dd-pathway-ctx": "test-pathway-ctx",
        }

        message_body = {
            "Type": "Notification",
            "MessageId": "test-message-id",
            "Message": "Test message from SNS",
            "MessageAttributes": {
                "_datadog": {
                    "Type": "Binary",
                    "Value": base64.b64encode(json.dumps(trace_context).encode("utf-8")).decode("utf-8"),
                }
            },
        }

        lambda_record = {
            "messageId": "lambda-message-id",
            "body": json.dumps(message_body),
            "eventSource": "aws:sqs",
            "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:sns-to-sqs-queue",
        }

        result = get_datastreams_context(lambda_record)

        assert result is not None
        assert result == trace_context
        assert result["x-datadog-trace-id"] == "123987456"
        assert result["x-datadog-parent-id"] == "654321987"
        assert result["dd-pathway-ctx"] == "test-pathway-ctx"

    # Edge case tests
    def test_no_message_attributes(self):
        """Test message without MessageAttributes returns None."""
        message = {"messageId": "test-message-id", "body": "Test message without attributes"}

        result = get_datastreams_context(message)

        assert result is None

    def test_no_datadog_attribute(self):
        """Test message with MessageAttributes but no _datadog attribute returns None."""
        message = {
            "messageId": "test-message-id",
            "body": "Test message",
            "messageAttributes": {"customAttribute": {"stringValue": "custom-value", "dataType": "String"}},
        }

        result = get_datastreams_context(message)
        assert result is None

    def test_empty_datadog_attribute(self):
        """Test message with empty _datadog attribute returns None."""
        message = {"messageId": "test-message-id", "messageAttributes": {"_datadog": {}}}

        result = get_datastreams_context(message)

        assert result is None
