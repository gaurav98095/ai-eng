# EdgentRAG agent guidance

## Project intent

This is a learning-oriented inference-engineering project. Preserve clear
seams so later experiments can replace retrieval, prompt packing, model
execution, transport, batching, kernels, or GPU serving independently.

## Before changing code

- Read the relevant numbered document in `app/docs/` and inspect nearby tests.
- Keep API routes thin; put behavior in a domain/service module.
- Keep hosted model calls behind protocols and injectable clients.
- Never commit tokens, private tunnel URLs, or generated model artifacts.

## Verification

From the repository root, run the focused tests first, then:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest app/backend/tests
.venv/bin/ruff check app/backend/src app/backend/tests
git diff --check
```

Update the relevant module Markdown whenever a public contract, command, or
architectural assumption changes. Explain deferred work rather than hiding it.

## Experiment discipline

Keep baselines runnable. Record model/configuration, hardware, software
versions, input sample or dataset identity, and measurement method for
optimization experiments. Prefer a new experiment module or implementation
behind dependency injection over changing the baseline in place.

See `app/docs/00-engineering-with-codex.md` for the learning workflow,
Markdown conventions, skills, and effective Codex requests.
