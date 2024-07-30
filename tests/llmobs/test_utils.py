import pytest

from ddtrace.llmobs.utils import Documents
from ddtrace.llmobs.utils import Messages


class Unserializable:
    pass


def test_messages_with_string():
    messages = Messages("hello")
    assert messages.messages == [{"content": "hello"}]


def test_messages_with_dict():
    messages = Messages({"content": "hello", "role": "user"})
    assert messages.messages == [{"content": "hello", "role": "user"}]


def test_messages_with_list_of_dicts():
    messages = Messages([{"content": "hello", "role": "user"}, {"content": "world", "role": "system"}])
    assert messages.messages == [{"content": "hello", "role": "user"}, {"content": "world", "role": "system"}]


def test_messages_with_incorrect_type():
    with pytest.raises(TypeError):
        Messages(123)
    with pytest.raises(TypeError):
        Messages(Unserializable())
    with pytest.raises(TypeError):
        Messages(None)


def test_messages_with_non_string_content():
    with pytest.raises(TypeError):
        Messages([{"content": 123}])
    with pytest.raises(TypeError):
        Messages([{"content": Unserializable()}])
    with pytest.raises(TypeError):
        Messages([{"content": None}])
    with pytest.raises(TypeError):
        Messages({"content": {"key": "value"}})


def test_messages_with_non_string_role():
    with pytest.raises(TypeError):
        Messages([{"content": "hello", "role": 123}])
    with pytest.raises(TypeError):
        Messages([{"content": "hello", "role": Unserializable()}])
    with pytest.raises(TypeError):
        Messages({"content": "hello", "role": {"key": "value"}})


def test_messages_with_no_role_is_ok():
    """Test that a message with no role is ok and returns a message with only content."""
    messages = Messages([{"content": "hello"}, {"content": "world"}])
    assert messages.messages == [{"content": "hello"}, {"content": "world"}]


def test_documents_with_string():
    documents = Documents("hello")
    assert documents.documents == [{"text": "hello"}]


def test_documents_with_dict():
    documents = Documents({"text": "hello", "name": "doc1", "id": "123", "score": 0.5})
    assert len(documents.documents) == 1
    assert documents.documents == [{"text": "hello", "name": "doc1", "id": "123", "score": 0.5}]


def test_documents_with_list_of_dicts():
    documents = Documents([{"text": "hello", "name": "doc1", "id": "123", "score": 0.5}, {"text": "world"}])
    assert len(documents.documents) == 2
    assert documents.documents[0] == {"text": "hello", "name": "doc1", "id": "123", "score": 0.5}
    assert documents.documents[1] == {"text": "world"}


def test_documents_with_incorrect_type():
    with pytest.raises(TypeError):
        Documents(123)
    with pytest.raises(TypeError):
        Documents(Unserializable())
    with pytest.raises(TypeError):
        Documents(None)


def test_documents_dictionary_no_text_value():
    with pytest.raises(TypeError):
        Documents([{"text": None}])
    with pytest.raises(TypeError):
        Documents([{"name": "doc1", "id": "123", "score": 0.5}])


def test_documents_dictionary_with_incorrect_value_types():
    with pytest.raises(TypeError):
        Documents([{"text": 123}])
    with pytest.raises(TypeError):
        Documents([{"text": [1, 2, 3]}])
    with pytest.raises(TypeError):
        Documents([{"text": "hello", "id": 123}])
    with pytest.raises(TypeError):
        Documents({"text": "hello", "name": {"key": "value"}})
    with pytest.raises(TypeError):
        Documents([{"text": "hello", "score": "123"}])
