from app.services import embeddings
from app.services.embeddings import embed_text, embed_texts, format_vector_literal


async def test_embed_texts_returns_none_when_voyage_not_configured(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "voyage_api_key", None)
    result = await embed_texts(["what is photosynthesis"], input_type="query")
    assert result is None


async def test_embed_texts_returns_none_for_empty_input(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "voyage_api_key", "test-key")
    result = await embed_texts([], input_type="document")
    assert result is None


async def test_embed_text_returns_first_vector_from_batch_call(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "voyage_api_key", "test-key")

    async def fake_embed_texts(texts, input_type):
        assert texts == ["why does ice float"]
        assert input_type == "query"
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)
    result = await embed_text("why does ice float", input_type="query")
    assert result == [0.1, 0.2, 0.3]


def test_format_vector_literal_matches_pgvector_text_input_format():
    assert format_vector_literal([0.1, -2.0, 3.5]) == "[0.1,-2.0,3.5]"
