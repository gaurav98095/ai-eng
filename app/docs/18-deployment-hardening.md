# Component 18: deployment and hardening scaffold

The final roadmap module now has a deliberately small container boundary:

- `app/backend/Dockerfile` packages the API and migrations into one image;
- `app/compose.yaml` runs the API, ingestion worker, and chat worker as
  separate processes from that image;
- `app/backend/.dockerignore` prevents local environments, tests, caches, and
  secrets from entering the build context.

The workers are separate services even though they share an image. This keeps
their scaling and resource limits independent, and makes it possible to move
each process to its own AWS service later without changing Python entrypoints.

Before using the compose file, provide a deployment-specific `.env` and apply
database migrations. Do not commit that file. Production should additionally
pin image versions, use a managed database and queue, terminate TLS at the
edge, restrict CORS, and provide explicit CPU/memory limits and worker
visibility-timeout monitoring.

Verification is intentionally a separate step: run the backend test suite,
Ruff, an image build, and a compose smoke test in CI or a controlled
environment rather than during source authoring.
