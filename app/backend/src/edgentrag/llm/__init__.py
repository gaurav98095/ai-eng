"""Application-facing LLM contracts, clients, and prompt policies.

The separately deployed model host lives in :mod:`edgentrag.generation`.
Everything in this package runs in the API and worker processes, so retrieval
and other domain services can depend on an LLM boundary without knowing how a
specific model is hosted.
"""
