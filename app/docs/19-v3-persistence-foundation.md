# V3 migration 1: persistent database foundation

The first v3-oriented slice hardens the database boundary without changing
route behavior:

- production configuration now rejects SQLite and requires
  `postgresql+asyncpg`;
- the backend includes the async PostgreSQL driver;
- PostgreSQL receives an explicit bounded connection pool with pre-ping;
- SQLite remains available for local tests and lightweight development.

This is intentionally separate from the upcoming outbox and worker changes.
Those features will depend on PostgreSQL transactions, but they should not be
introduced in the same migration as the connection-policy change.

The next slice should add repository-level transaction services and then a
transactional outbox table. Until that is implemented, queue publication and
database commits remain separate operations as documented in Component 13.
