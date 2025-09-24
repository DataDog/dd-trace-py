import mock
import pytest


@pytest.mark.snapshot
def test_fake_embedding_query(langchain_community):
    embeddings = langchain_community.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_query(text="foo")


@pytest.mark.snapshot
def test_fake_embedding_document(langchain_community):
    embeddings = langchain_community.embeddings.FakeEmbeddings(size=99)
    embeddings.embed_documents(texts=["foo", "bar"])


@pytest.mark.snapshot
def test_faiss_vectorstore_retrieval(langchain_community, langchain_openai, openai_url):
    pytest.importorskip("faiss", reason="faiss required for this test.")
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[[0.0] * 1536]):
        faiss = langchain_community.vectorstores.faiss.FAISS.from_texts(
            ["this is a test query."],
            embedding=langchain_openai.OpenAIEmbeddings(base_url=openai_url),
        )
        retriever = faiss.as_retriever()
        retriever.invoke("What was the message of the last test query?")


@pytest.mark.snapshot
def test_openai_embedding_query(langchain_openai, openai_url):
    with mock.patch("langchain_openai.OpenAIEmbeddings._get_len_safe_embeddings", return_value=[0.0] * 1536):
        embeddings = langchain_openai.OpenAIEmbeddings(base_url=openai_url)
        embeddings.embed_query("this is a test query.")
