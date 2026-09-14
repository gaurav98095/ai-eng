# EdgentRAG backend

This directory is rebuilt component by component. The first component is the
API application shell and its health endpoint.

Run it locally from the repository root:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e "./app/backend[dev]"
cp app/backend/.env.example app/backend/.env
.venv/bin/alembic -c app/backend/alembic.ini upgrade head
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

Open http://127.0.0.1:8000/docs to inspect the generated API documentation.

Run the quality checks:

~~~bash
.venv/bin/python -m ruff check app/backend
.venv/bin/python -m pytest app/backend
~~~

Create a session and then request a presigned upload target:

~~~bash
SESSION_ID=$(
  curl -s -X POST http://127.0.0.1:8000/sessions \
    | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["session_id"])'
)
curl -s -X POST "http://127.0.0.1:8000/sessions/$SESSION_ID/uploads" \
  -H 'Content-Type: application/json' \
  -d '{"files":[{"filename":"notes.md","content_type":"text/markdown","size_bytes":4096}]}'
~~~

Real S3 upload URLs require EDGENTRAG_S3_BUCKET and AWS credentials from the
standard AWS credential chain (or an EC2 instance role). Tests use fake storage
and do not need AWS access.

The editable installation is required. Application code lives in
backend/src/edgentrag, which deliberately is not on Python's import path by
default. Installing the project makes the package importable and ensures local
development behaves like a deployed package. Settings load app/backend/.env,
independent of the shell's current directory.

For a one-off run without installing the project, point Uvicorn at the source
directory explicitly:

~~~bash
.venv/bin/python -m uvicorn --app-dir app/backend/src \
  edgentrag.api.app:app --reload
~~~

The application uses a src layout: importable application code belongs in
src/edgentrag, while tests belong in tests. This prevents tests from
accidentally importing files from the repository root instead of the installed
package.
