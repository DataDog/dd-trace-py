from re import match
from hashlib import sha1
from typing import Dict, Tuple, Optional
from typing import List
from typing import Union
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS

# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

DocumentType = Dict[str, Union[str, int, float]]

ExportedLLMObsSpan = TypedDict("ExportedLLMObsSpan", {"span_id": str, "trace_id": str})
Document = TypedDict("Document", {"name": str, "id": str, "text": str, "score": float}, total=False)
Message = TypedDict("Message", {"content": str, "role": str}, total=False)

class Prompt:
    """
    Represents a prompt used for an LLM call.

    Attributes:
        name (str): The name of the prompt.
        version (str): The version of the prompt.
        prompt_template_id (int): A hash of name and ml_app, used to identify the prompt template.
        prompt_instance_id (int): A hash of all prompt attributes, used to identify the prompt instance.
        template (Union[List[Tuple[str, str]], str]): The template used for the prompt, which can be a list of tuples or a string.
        variables (Dict[str, str]): A dictionary of variables used in the prompt.
        example_variable_keys (List[str]): A list of variables names denoting examples. Examples are used to improve accuracy for the prompt.
        constraint_variable_keys (List[str]): A list of variables names denoting constraints. Constraints are limitations on how the prompt result is displayed.
        rag_context_variable_keys (List[str]): A list of variable key names that contain ground truth context information.
        rag_query_variable_keys (List[str]): A list of variable key names that contain query information for an LLM call.
    """
    name: str
    version: Optional[str]
    prompt_template_id: str
    prompt_instance_id: str
    template: Optional[List[Tuple[str, str]]]
    variables: Optional[Dict[str, str]]
    example_variable_keys: Optional[List[str]]
    constraint_variable_keys: Optional[List[str]]
    rag_context_variable_keys: Optional[List[str]]
    rag_query_variable_keys: Optional[List[str]]

    def __init__(self,
                 name,
                 version = "1.0.0",
                 template = None,
                 variables = None,
                 example_variable_keys = None,
                 constraint_variable_keys = None,
                 rag_context_variable_keys = None,
                 rag_query_variable_keys = None):

        if name is None:
            raise TypeError("Prompt name of type String is mandatory.")

        self.name = name

        # Default values
        template = template or []
        variables = variables or {}
        # TODO remove default keys when not in variables
        example_variable_keys = example_variable_keys or ["example"]
        constraint_variable_keys = constraint_variable_keys or ["constraint"]
        rag_context_variable_keys = rag_context_variable_keys or ["context"]
        rag_query_variable_keys = rag_query_variable_keys or ["question"]
        version = version or "1.0.0"

        if version is not None:
            # Add minor and patch version if not present
            version_parts = (version.split(".") + ["0", "0"])[:3]
            version = ".".join(version_parts)

        # Accept simple string templates
        if isinstance(template, str):
            template = [("user", template)]

        self.version = version
        self.template = template
        self.variables = variables
        self.example_variables = example_variables
        self.constraint_variables = constraint_variables
        self.rag_context_variables = rag_context_variables
        self.rag_query_variables = rag_query_variables

    def to_tags_dict(self) -> Dict[str, Union[str, int, List[str], Dict[str, str], List[Tuple[str, str]]]]:
        return {
            "name": self.name,
            "version": self.version,
            "prompt_template_id": self.prompt_template_id,
            "prompt_instance_id": self.prompt_instance_id,
            "template": self.template,
            "variables": self.variables,
            "example_variable_keys": self.example_variable_keys,
            "constraint_variable_keys": self.constraint_variable_keys,
            INTERNAL_CONTEXT_VARIABLE_KEYS: self.rag_context_variable_keys,
            INTERNAL_QUERY_VARIABLE_KEYS: self.rag_query_variable_keys,
        }

    def generate_ids(self, ml_app=""):
        """
        Generates prompt_template_id and prompt_instance_id based on the prompt attributes.
        The prompt_template_id is a sha-1 hash of the prompt name and ml_app
        The prompt_instance_id is a sha-1 hash of all prompt attributes.
        """
        name = str(self.name)
        version = str(self.version)
        template = str(self.template)
        variables = str(self.variables)
        example_variable_keys = str(self.example_variable_keys)
        constraint_variable_keys = str(self.constraint_variable_keys)
        rag_context_variable_keys = str(self.rag_context_variable_keys)
        rag_query_variable_keys = str(self.rag_query_variable_keys)

        template_id_str = f"[{ml_app}]{name}"
        instance_id_str = f"[{ml_app}]{name}{version}{template}{variables}{example_variable_keys}{constraint_variable_keys}{rag_context_variable_keys}{rag_query_variable_keys}"

        self.prompt_template_id = sha1(template_id_str.encode()).hexdigest()
        self.prompt_instance_id = sha1(instance_id_str.encode()).hexdigest()

    def validate(self):
        errors = []
        prompt_template_id = self.prompt_template_id
        prompt_instance_id = self.prompt_instance_id
        name = self.name
        version = self.version
        template = self.template
        variables = self.variables
        example_variable_keys = self.example_variable_keys
        constraint_variable_keys = self.constraint_variable_keys
        rag_context_variable_keys = self.rag_context_variable_keys
        rag_query_variable_keys = self.rag_query_variable_keys


        if prompt_template_id is None:
            self.generate_ids()
        elif not isinstance(prompt_template_id, str):
            errors.append("Prompt template id must be a string.")
        if prompt_instance_id is None:
            self.generate_ids()
        elif not isinstance(prompt_instance_id, str):
            errors.append("Prompt instance id must be a string.")

        if name is None:
            errors.append("Prompt name of type String is mandatory.")
        elif not isinstance(name, str):
            errors.append("Prompt name must be a string.")

        if version is not None:
            # Add minor and patch version if not present
            version_parts = (version.split(".") + ["0", "0"])[:3]
            version = ".".join(version_parts)
            # Official semver regex from https://semver.org/
            semver_regex = (
                r'^(?P<major>0|[1-9]\d*)\.'
                r'(?P<minor>0|[1-9]\d*)\.'
                r'(?P<patch>0|[1-9]\d*)'
                r'(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-]'
                r'[0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-]'
                r'[0-9a-zA-Z-]*))*))?'
                r'(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+'
                r'(?:\.[0-9a-zA-Z-]+)*))?$'
            )
            if not bool(match(semver_regex, version)):
                errors.append(
                    "Prompt version must be semver compatible. Please check https://semver.org/ for more information.")

        # Accept simple string templates
        if isinstance(template, str):
            template = [("user", template)]

        # validate template
        if not (isinstance(template, list) and all(isinstance(t, tuple) for t in template)):
            errors.append("Prompt template must be a list of tuples.")
        if not all(len(t) == 2 for t in template):
            errors.append("Prompt template tuples must have exactly two elements.")
        if not all(isinstance(item[0], str) and isinstance(item[1], str) for item in template):
            errors.append("Prompt template tuple elements must be strings.")

        if not isinstance(variables, dict):
            errors.append("Prompt variables must be a dictionary.")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in variables.items()):
            errors.append("Prompt variable keys and values must be strings.")

        for var_list in [example_variable_keys, constraint_variable_keys, rag_context_variable_keys, rag_query_variable_keys]:
            if not all(isinstance(var, str) for var in var_list):
                errors.append("All variable lists must contain strings only.")

        if errors:
            raise TypeError("\n".join(errors))

        return errors


class Messages:
    def __init__(self, messages: Union[List[Dict[str, str]], Dict[str, str], str]):
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
            if not role:
                self.messages.append(Message(content=content))
                continue
            if not isinstance(role, str):
                raise TypeError("Message role must be a string, and one of .")
            self.messages.append(Message(content=content, role=role))


class Documents:
    def __init__(self, documents: Union[List[DocumentType], DocumentType, str]):
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
