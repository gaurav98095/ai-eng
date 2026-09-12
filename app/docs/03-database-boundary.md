# Component 3: database boundary and readiness

This component adds a database connection without adding application tables.
That separation is intentional: first we establish how the application owns
and tests a database resource; the next component will define the first data
model and migration.

## What changed

~~~text
FastAPI lifespan starts
        │
        ▼
Database(database_url) creates one async engine
        │
        ▼
/ready runs SELECT 1
        │
        ├── success → 200, database: ok
        └── failure → 503, database: failed
~~~

## The database URL

For local development, .env.example uses SQLite:

~~~ini
EDGENTRAG_DATABASE_URL=sqlite+aiosqlite:///./edgentrag.db
~~~

Make the local environment file from the checked-in template once:

~~~bash
cp app/backend/.env.example app/backend/.env
~~~

Settings resolve that file relative to the backend package, so launching
Uvicorn from the repository root still loads it.

SQLite is useful at this stage because it needs no separate service. The
database URL is configuration rather than a hard-coded implementation detail,
so a later deployment can use PostgreSQL without changing routes or models.
The local database file is ignored by Git.

## Why async SQLAlchemy

FastAPI request handlers are asynchronous. SQLAlchemy's async engine lets a
route wait for database I/O without blocking the event loop. The Database class
owns the engine and session factory, while routes use FastAPI dependencies to
receive that resource. This prevents each route from silently creating its own
connection pool.

## Lifespan ownership

The application factory creates the Database during its lifespan and disposes
it when the process stops:

~~~text
application startup → create database resource
application running → routes reuse that resource
application shutdown → dispose connections
~~~

This is the correct ownership boundary for connection pools, HTTP clients, and
other resources that must be closed. A future component will add a
per-request database-session dependency; a session is shorter-lived than the
engine and must not be shared across requests.

## Readiness versus liveness

The /health endpoint still always returns 200 when the API process is alive.
The /ready endpoint now makes a real SELECT 1 query:

~~~json
{
  "status": "ready",
  "environment": "local",
  "checks": {
    "configuration": "ok",
    "database": "ok"
  }
}
~~~

If the configured database is unavailable, /ready returns HTTP 503 and says
database is failed. This lets a deployment system stop routing new traffic
without repeatedly restarting a process that may otherwise be healthy.

## Tests

The readiness test creates a SQLite database in pytest's temporary directory.
It proves the route executes a real query without touching the developer's
local database. That test isolation is a habit worth keeping as the schema and
features grow.

Run the checks from the repository root:

~~~bash
.venv/bin/python -m pip install -e "./app/backend[dev]"
.venv/bin/python -m ruff check app/backend
.venv/bin/python -m pytest app/backend
~~~

## What comes next

The next component adds the first domain model, an initial migration, and a
database-session dependency. We will not let routes write raw SQL or create
tables on startup.
