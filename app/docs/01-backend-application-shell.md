# Component 1: backend application shell

This is the first tutorial in the rebuild. We will deliberately write one
small, complete component at a time. Each tutorial adds code, explains why it
belongs where it does, and leaves the app runnable and tested.

## The roadmap

1. **Backend application shell** — complete.
2. Backend settings and dependency wiring.
3. Database connection and readiness.
4. First domain model, migration, and session API.
5. File-upload API and S3 storage adapter.
6. Queue contracts and ingestion worker.
7. Colab broker and model-service client.
8. Chat API and worker — complete ([Component 14](14-chat-worker.md)).
9. React application shell — not present in this replica yet.
10. React service connection screen — not present in this replica yet.
11. Upload, processing-status, and chat UI components — not present in this replica yet.
12. Docker, AWS, Colab, tests, and deployment hardening.

The order matters. We first create stable boundaries, then add one capability
behind each boundary. We do not start with AWS or the frontend because both
would otherwise depend on code that has not been designed yet.

## What we are building now

The first component is the smallest backend that can truthfully say it is
alive:

~~~text
HTTP GET /health
        │
        ▼
FastAPI route
        │
        ▼
{"status": "ok"}
~~~

This is a **liveness** check. It only proves that the API process can receive
and return an HTTP request. It must not call Redis, S3, SQS, the database, or
Colab. If it did, a temporary dependency failure could make a healthy API
process appear dead and trigger unnecessary restarts.

## Files written

~~~text
backend/
├── pyproject.toml
├── src/
│   └── edgentrag/
│       └── api/
│           ├── app.py
│           └── routes/health.py
└── tests/
    └── test_health.py
~~~

### pyproject.toml

This is the project contract: supported Python version, runtime dependencies,
development dependencies, test discovery, and linting rules. A single
declarative file is easier to reproduce than an unpinned collection of install
commands.

### src/edgentrag/api/app.py

This module contains the application factory. The factory makes isolated tests
easy: a test can make a new application rather than accidentally sharing a
global application modified by another test. The final app variable is the
ASGI object that Uvicorn serves.

### src/edgentrag/api/routes/health.py

Routes are grouped by feature rather than placed in one large application file.
The Pydantic response model is deliberate: it documents and validates the
response contract, and FastAPI exposes it in /docs automatically.

### tests/test_health.py

The test makes an HTTP request through FastAPI's test client. It verifies both
the status code and the exact JSON contract. This protects callers from a
seemingly harmless future change such as returning a different field name.

## Run it

From the repository root:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e "./app/backend[dev]"
.venv/bin/python -m ruff check app/backend
.venv/bin/python -m pytest app/backend
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

Then visit:

- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs

Expected health response:

~~~json
{"status": "ok"}
~~~

### Why did Uvicorn say "No module named edgentrag"?

The project follows a src layout: the Python package is in
app/backend/src/edgentrag, not directly in the repository root. That is a
useful safeguard against accidental imports from the checkout, but it means
the package must be installed before Uvicorn can import it.

The editable install command above creates that import link. If you only need
to prove the server starts before installing, use:

~~~bash
.venv/bin/python -m uvicorn --app-dir app/backend/src \
  edgentrag.api.app:app --reload
~~~

## What we intentionally did not add

There is no database, AWS client, environment-variable class, authentication,
or frontend yet. Adding them now would make this tutorial harder to understand
and create dependencies before a feature needs them.

The next tutorial will add configuration and dependency wiring. It will
introduce a readiness endpoint separately from this liveness endpoint.
