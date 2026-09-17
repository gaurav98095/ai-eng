# Component 2: typed settings and readiness

> **Historical v2 walkthrough.** Use [Current architecture and operating guide](22-current-architecture.md)
> for the current Compose-owned configuration and Make commands.

The API now has two health concepts:

~~~text
GET /health  → "Is the API process alive?"
GET /ready   → "Are the configuration and database ready?"
~~~

They are deliberately different. A liveness endpoint is safe for a container
or process supervisor to use when deciding whether to restart the process. It
does not touch a database, S3, SQS, Redis, or Colab. The readiness endpoint
now checks the configuration and database; future components will add real
checks as those dependencies are introduced.

## What we wrote

~~~text
backend/
├── .env.example
└── src/edgentrag/
    ├── core/config.py
    └── api/routes/health.py
~~~

## Settings are a boundary

All configuration goes through one Settings class. It reads environment
variables with the EDGENTRAG_ prefix:

~~~ini
EDGENTRAG_ENVIRONMENT=local
EDGENTRAG_LOG_LEVEL=INFO
~~~

The .env.example file is safe to commit because it contains only non-secret
defaults. A real .env file is local-only and belongs in .gitignore. When we
introduce AWS credentials, the server will use an IAM role rather than storing
keys in this file.

The settings type restricts environment and log-level values. A typo such as
EDGENTRAG_ENVIRONMENT=prod is rejected during startup instead of quietly
creating a fourth, unsupported environment.

## Dependency injection

The ready route receives settings through FastAPI's Depends mechanism:

~~~text
GET /ready
    │
    ├── FastAPI resolves get_settings
    │
    ├── FastAPI resolves get_database
    └── route receives typed settings and the app's Database resource
~~~

The cached load_settings function creates one settings object per process. The
application keeps that immutable snapshot in application state, and a FastAPI
dependency reads it for each route. This avoids a test relying on a developer's
.env file and is the pattern we will use for database sessions, S3 clients,
queue clients, and model-service clients.

## Run and test

From the repository root:

~~~bash
.venv/bin/python -m pip install -e "./app/backend[dev]"
cp app/backend/.env.example app/backend/.env
.venv/bin/python -m ruff check app/backend
.venv/bin/python -m pytest app/backend
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

Try both endpoints:

~~~bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
~~~

Expected output:

~~~json
{"status":"ok"}
~~~

~~~json
{"status":"ready","environment":"local","checks":{"configuration":"ok","database":"ok"}}
~~~

If the database is unreachable, /ready returns HTTP 503 and marks the database
check as failed. Component 3's tutorial explains the database boundary and
application lifespan in detail.
