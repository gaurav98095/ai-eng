# Component 4: first domain model and session API

We now have a database connection, so this component gives the app its first
piece of durable domain data: a chat session. A session is created before
uploads; future components will attach files, processing state, and messages
to it.

~~~text
POST /sessions
    │
    ▼
FastAPI yields one request-scoped AsyncSession
    │
    ▼
INSERT chat_sessions(...)
    │
    ▼
201 Created + session ID
~~~

## Model and migration are different things

The SQLAlchemy model in sessions/models.py describes the table to Python. The
Alembic revision in migrations/versions describes how a database changes from
its previous schema to this one:

- Base.metadata is the collection of model table definitions.
- Alembic applies reviewed, ordered schema changes.
- The application does not create or alter tables on startup.

That last point matters. Startup should not race to change production schema
when multiple app processes restart at once. Run migrations as an explicit
deployment step.

The model stores a UUID string, a constrained lifecycle status, and a creation
timestamp. A database check constraint rejects invalid status values even if
a future caller bypasses Pydantic and writes directly to the database.

## Request-scoped sessions

Database owns the long-lived engine and session factory. A request borrows a
short-lived AsyncSession using the get_db_session dependency. The dependency
closes that session after the request, including when the route raises an
error.

Do not share an AsyncSession between requests: it represents one unit of work,
may hold transaction state, and is not safe for concurrent use.

The route explicitly commits before responding. If the insert fails, the
request fails instead of returning an ID for a row that was never saved.
Refresh reads persisted values back before building the response.

## Migrations

Alembic reads the same EDGENTRAG_DATABASE_URL setting as the API. Create the
local database schema with:

~~~bash
.venv/bin/alembic -c app/backend/alembic.ini upgrade head
~~~

Inspect differences between models and the current migration with:

~~~bash
.venv/bin/alembic -c app/backend/alembic.ini check
~~~

For each schema change, create and review a migration, then deploy it before
code that requires the new columns. To intentionally undo this first migration
in a disposable local database:

~~~bash
.venv/bin/alembic -c app/backend/alembic.ini downgrade base
~~~

Never downgrade a database containing data without first understanding what
the revision's downgrade drops.

## Try the API

Start the API after applying migrations:

~~~bash
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

Create a session:

~~~bash
curl -X POST http://127.0.0.1:8000/sessions
~~~

The response is HTTP 201 and includes a generated session ID, created status,
and creation timestamp. Calling the endpoint twice creates two different
session records.

## Test isolation

The integration test creates a temporary SQLite database, applies the actual
Alembic revision, calls the HTTP endpoint, and reads the inserted row back.
This checks the whole path while leaving the developer's local database
untouched.

Run quality checks from the repository root:

~~~bash
.venv/bin/python -m pip install -e "./app/backend[dev]"
.venv/bin/python -m ruff check app/backend
.venv/bin/python -m pytest app/backend
~~~

## What comes next

Component 5 adds file metadata and private object-storage URLs. The browser
upload bytes stay out of the API process.
