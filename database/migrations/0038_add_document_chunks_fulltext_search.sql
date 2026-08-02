-- Backs real textbook grounding for the tutor (see
-- app.services.retrieval) — retrieval uses Postgres full-text search
-- rather than a vector embedding store, since no embeddings provider is
-- wired up anywhere in this codebase yet (chromadb isn't even in
-- requirements.txt despite CHROMA_PERSIST_DIR existing as a setting).
-- Full-text search needs zero new API keys/dependencies and works well
-- for well-structured textbook chapter content; can be upgraded to real
-- vector search later without changing the documents/document_chunks
-- schema itself.
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;
CREATE INDEX IF NOT EXISTS idx_document_chunks_content_tsv ON document_chunks USING GIN (content_tsv);
