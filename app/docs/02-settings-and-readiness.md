# Component 2: typed settings and readiness

The API now has two health concepts:

~~~text
GET /health  → "Is the API process alive?"
GET /ready   → "Has the configuration required by this version loaded?"
~~~

They are deliberately different. A liveness endpoint is safe for a container
or process supervisor to use when deciding whether to restart the process. It
does not touch a database, S3, SQS, Redis, or Colab. A future readiness check
will check those dependencies before a load balancer sends user traffic to the
API.

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
    └── route receives a typed Settings object
~~~

The cache in get_settings means one settings object is made per process. The
test replaces only this dependency with test settings; it never relies on a
developer's .env file. This is the small version of a pattern we will use for
database sessions, S3 clients, queue clients, and model-service clients.

## Run and test

From the repository root:

~~~bash
.venv/bin/python -m pip install -e "./app/backend[dev]"
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
{"status":"ready","environment":"local","checks":{"configuration":"ok"}}
~~~

## Why database checks are not here yet

It would be tempting to add a database connection now, but there is no database
component yet. Returning a pretend database check would be worse than not
having one. The next tutorial will add the data layer, then readiness will gain
a real database check.

