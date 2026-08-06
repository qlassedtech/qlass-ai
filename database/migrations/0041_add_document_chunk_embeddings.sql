-- Voyage-embedding vector column for semantic (not just keyword) textbook
-- retrieval — see app.services.embeddings/app.services.retrieval. Column
-- is written/read via raw SQL rather than the SQLAlchemy ORM (see
-- app.models.core's comment above the matching DDL event) since the
-- pgvector Python package isn't a dependency here.
--
-- Dimension (1024) must match settings.voyage_embedding_dimensions —
-- changing either one requires re-embedding every existing chunk.
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
