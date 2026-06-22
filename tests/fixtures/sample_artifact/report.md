# Synthetic RAG PoC (fixture)

This is a hygiene-clean fixture mirroring a Stage-2 run folder's companion `report.md` — used by
poc-foundry's contract tests + ingest probe. It is NOT a real research run.

## Architecture

A gradio chat over a labeled mini-corpus, retrieving from pgvector and answering with citations.

## Sources

1. [example/rag-demo](https://github.com/example/rag-demo)
