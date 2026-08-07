from typing import Any
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.types import AudioPart
from ddtrace.llmobs.types import Document
from ddtrace.llmobs.types import ImagePart
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolCall
from ddtrace.llmobs.types import ToolDefinition
from ddtrace.llmobs.types import ToolResult


log = get_logger(__name__)

DocumentType = dict[str, Union[str, int, float]]


def _extract_tool_call(tool_call: dict[str, Any]) -> "ToolCall":
    """Extract and validate a tool call dictionary."""
    if not isinstance(tool_call, dict):
        raise TypeError("Each tool_call must be a dictionary.")

    # name and arguments are required
    name = tool_call.get("name")
    arguments = tool_call.get("arguments")

    if not name or not isinstance(name, str):
        raise TypeError("ToolCall name must be a non-empty string.")
    if arguments is None or not isinstance(arguments, dict):
        raise TypeError("ToolCall arguments must be a dictionary.")

    formatted_tool_call = ToolCall(name=name, arguments=arguments)

    # Add optional fields if present
    tool_id = tool_call.get("tool_id")
    if tool_id and isinstance(tool_id, str):
        formatted_tool_call["tool_id"] = tool_id

    tool_type = tool_call.get("type")
    if tool_type and isinstance(tool_type, str):
        formatted_tool_call["type"] = tool_type

    return formatted_tool_call


def _extract_tool_result(tool_result: dict[str, Any]) -> "ToolResult":
    """Extract and validate a tool result dictionary."""
    if not isinstance(tool_result, dict):
        raise TypeError("Each tool_result must be a dictionary.")

    # result is required
    result = tool_result.get("result")
    if result is None or not isinstance(result, str):
        raise TypeError("ToolResult result must be a string.")

    formatted_tool_result = ToolResult(result=result)

    # Add optional fields if present
    name = tool_result.get("name")
    if name and isinstance(name, str):
        formatted_tool_result["name"] = name

    tool_id = tool_result.get("tool_id")
    if tool_id and isinstance(tool_id, str):
        formatted_tool_result["tool_id"] = tool_id

    tool_type = tool_result.get("type")
    if tool_type and isinstance(tool_type, str):
        formatted_tool_result["type"] = tool_type

    return formatted_tool_result


def _extract_audio_part(audio_part: dict[str, Any]) -> "AudioPart":
    """Extract and validate an audio part dictionary."""
    if not isinstance(audio_part, dict):
        raise TypeError("Each audio_part must be a dictionary.")

    mime_type = audio_part.get("mime_type")
    if not mime_type or not isinstance(mime_type, str):
        raise TypeError("AudioPart mime_type must be a non-empty string.")

    # exactly one of content / attachment_key
    content = audio_part.get("content")
    attachment_key = audio_part.get("attachment_key")
    if content is None and attachment_key is None:
        raise TypeError("AudioPart must have either 'content' or 'attachment_key'.")
    if content is not None and attachment_key is not None:
        raise TypeError("AudioPart must have only one of 'content' or 'attachment_key', not both.")

    formatted_audio_part = AudioPart(mime_type=mime_type)
    if content is not None:
        if not isinstance(content, str):
            raise TypeError("AudioPart content must be a base64-encoded string.")
        formatted_audio_part["content"] = content
    if attachment_key is not None:
        if not isinstance(attachment_key, str):
            raise TypeError("AudioPart attachment_key must be a string.")
        formatted_audio_part["attachment_key"] = attachment_key

    return formatted_audio_part


def _extract_image_part(image_part: dict[str, Any]) -> "ImagePart":
    """Extract and validate an image part dictionary."""
    if not isinstance(image_part, dict):
        raise TypeError("Each image_part must be a dictionary.")

    mime_type = image_part.get("mime_type")
    if not mime_type or not isinstance(mime_type, str):
        raise TypeError("ImagePart mime_type must be a non-empty string.")

    # exactly one of content / attachment_key
    content = image_part.get("content")
    attachment_key = image_part.get("attachment_key")
    if content is None and attachment_key is None:
        raise TypeError("ImagePart must have either 'content' or 'attachment_key'.")
    if content is not None and attachment_key is not None:
        raise TypeError("ImagePart must have only one of 'content' or 'attachment_key', not both.")

    formatted_image_part = ImagePart(mime_type=mime_type)
    if content is not None:
        if not isinstance(content, str):
            raise TypeError("ImagePart content must be a base64-encoded string.")
        formatted_image_part["content"] = content
    if attachment_key is not None:
        if not isinstance(attachment_key, str):
            raise TypeError("ImagePart attachment_key must be a string.")
        formatted_image_part["attachment_key"] = attachment_key

    return formatted_image_part


def extract_tool_definitions(tool_definitions: list[dict[str, Any]]) -> list[ToolDefinition]:
    """Return a list of validated tool definitions."""
    if not isinstance(tool_definitions, list):
        log.warning("tool_definitions must be a list of dictionaries.")
        return []

    validated_tool_definitions = []
    for i, tool_def in enumerate(tool_definitions):
        if not isinstance(tool_def, dict):
            log.warning("Tool definition at index %s must be a dictionary. Skipping.", i)
            continue

        # name is required
        name = tool_def.get("name")
        if not name or not isinstance(name, str):
            log.warning("Tool definition at index %s must have a non-empty 'name' field. Skipping.", i)
            continue

        validated_tool_def = ToolDefinition(name=name)

        # description is optional
        description = tool_def.get("description")
        if description is not None:
            if not isinstance(description, str):
                log.warning(
                    "Tool definition 'description' at index %s must be a string. Skipping description field.", i
                )
            else:
                validated_tool_def["description"] = description

        # schema is optional
        schema = tool_def.get("schema")
        if schema is not None:
            if not isinstance(schema, dict):
                log.warning("Tool definition 'schema' at index %s must be a dictionary. Skipping schema field.", i)
            else:
                validated_tool_def["schema"] = schema

        # version is optional
        version = tool_def.get("version")
        if version is not None:
            if not isinstance(version, str):
                log.warning("Tool definition 'version' at index %s must be a string. Skipping version field.", i)
            else:
                validated_tool_def["version"] = version

        validated_tool_definitions.append(validated_tool_def)

    return validated_tool_definitions


class Messages:
    def __init__(self, messages: Union[list[dict[str, Any]], dict[str, Any], str]):
        self.messages = []
        if not isinstance(messages, list):
            messages = [messages]  # type: ignore[list-item]
        for message in messages:
            if isinstance(message, str):
                self.messages.append(Message(content=message))
                continue
            elif not isinstance(message, dict):
                raise TypeError("messages must be a string, dictionary, or list of dictionaries.")

            content = message.get("content", "")
            role = message.get("role")
            if not isinstance(content, str):
                raise TypeError("Message content must be a string.")

            msg_dict = Message(content=content)
            if role:
                if not isinstance(role, str):
                    raise TypeError("Message role must be a string.")
                msg_dict["role"] = role

            tool_calls = message.get("tool_calls")
            if tool_calls is not None:
                if not isinstance(tool_calls, list):
                    raise TypeError("tool_calls must be a list.")
                formatted_tool_calls = [_extract_tool_call(tool_call) for tool_call in tool_calls]
                msg_dict["tool_calls"] = formatted_tool_calls

            tool_results = message.get("tool_results")
            if tool_results is not None:
                if not isinstance(tool_results, list):
                    raise TypeError("tool_results must be a list.")
                formatted_tool_results = [_extract_tool_result(tool_result) for tool_result in tool_results]
                msg_dict["tool_results"] = formatted_tool_results

            audio_parts = message.get("audio_parts")
            if audio_parts is not None:
                if not isinstance(audio_parts, list):
                    raise TypeError("audio_parts must be a list.")
                formatted_audio_parts = [_extract_audio_part(audio_part) for audio_part in audio_parts]
                msg_dict["audio_parts"] = formatted_audio_parts

            image_parts = message.get("image_parts")
            if image_parts is not None:
                if not isinstance(image_parts, list):
                    raise TypeError("image_parts must be a list.")
                formatted_image_parts = [_extract_image_part(image_part) for image_part in image_parts]
                msg_dict["image_parts"] = formatted_image_parts

            self.messages.append(msg_dict)


class Documents:
    def __init__(self, documents: Union[list[DocumentType], DocumentType, str]):
        self.documents = []
        if not isinstance(documents, list):
            documents = [documents]  # type: ignore[list-item]
        for document in documents:
            if isinstance(document, str):
                self.documents.append(Document(text=document))
                continue
            elif not isinstance(document, dict):
                raise TypeError("documents must be a string, dictionary, or list of dictionaries.")
            document_text = document.get("text")
            document_name = document.get("name")
            document_id = document.get("id")
            document_score = document.get("score")
            if not isinstance(document_text, str):
                raise TypeError("Document text must be a string.")
            formatted_document = Document(text=document_text)
            if document_name:
                if not isinstance(document_name, str):
                    raise TypeError("document name must be a string.")
                formatted_document["name"] = document_name
            if document_id:
                if not isinstance(document_id, str):
                    raise TypeError("document id must be a string.")
                formatted_document["id"] = document_id
            if document_score:
                if not isinstance(document_score, (int, float)):
                    raise TypeError("document score must be an integer or float.")
                formatted_document["score"] = document_score
            self.documents.append(formatted_document)
