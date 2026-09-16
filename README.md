# EdgentRAG

A learning-oriented document question-answering application built to explore
retrieval and model-serving boundaries. Upload documents, index their contents,
and ask questions backed by source excerpts.

The application lives in [`app/`](app/README.md). It includes a React frontend,
FastAPI API, background workers, PostgreSQL/pgvector, and Redis. Embedding and
generation run as separate model services; local AWS-compatible storage and
queues use an independently started Floci container.

- [Application guide and local setup](app/README.md)
- [Colab and Lightning model-service setup](app/docs/model-service-hosting.md)
- [Engineering walkthroughs](app/docs/00-engineering-with-codex.md)
- [Deployment templates and limitations](app/README.md#production)

This is an experimental baseline, not a production-ready deployment. See the
application guide for current limitations before using it with real users or
sensitive documents.
