# Learning track: software engineering with Codex

This project teaches two things at once: inference engineering and the habits
that keep an AI-assisted codebase understandable. Every module should leave
behind working code, tests, documentation, and a reproducible way to run it.

## Use Codex as a collaborator, not an unchecked autocomplete tool

Give Codex a concrete outcome, the files or subsystem in scope, constraints,
and the verification command. For example:

```text
Add a bounded answer endpoint under app/backend/src/edgentrag/retrieval.
Keep generation behind a protocol so tests do not need a hosted model.
Add focused tests and update the next module document. Run pytest and Ruff.
```

Ask for diagnosis before implementation when you are investigating a bug. Ask
for a plan when the change spans several boundaries. Ask Codex to review a diff
for correctness, security, and missing tests before committing. Keep requests
small enough that the resulting diff can be understood in one sitting.

## What `AGENTS.md` is for

`AGENTS.md` is repository-local operating guidance for Codex and other coding
agents. It should contain durable rules, not a changelog or a description of a
single feature. Useful content includes:

- repository layout and the commands that verify it;
- architectural boundaries and files that must not be coupled;
- security rules for secrets, hosted services, and data;
- formatting, test, migration, and commit conventions;
- how to handle generated files and local experiments.

Keep instructions close to the code they govern. A root `AGENTS.md` applies to
the repository; a deeper file can add rules for one subsystem. If instructions
conflict, the more local guidance should be considered for that subtree, while
user requirements always win. Prefer short, testable rules and update them
when the workflow changes.

## What a skill is for

A skill is reusable procedural knowledge for an agent: when to use a tool,
which references to read, and what quality or safety checks to perform. Use a
skill for a recurring workflow (for example, adding a migration or evaluating
an inference benchmark), not for ordinary project facts. Keep skills narrowly
scoped, versioned with their supporting scripts/templates, and explicit about
inputs, outputs, and failure modes. The repository's `AGENTS.md` should point
to relevant skills; it should not copy their entire contents.

## Maintain Markdown as part of the product

Each numbered module should answer the same questions: what changed, why the
boundary exists, how the request/data flow works, how to run it, how to test it,
and what is intentionally deferred. Link to source files and commands instead
of duplicating implementation. When behavior changes, update the module that
introduced it and the next module's assumptions. Keep a small index or clear
numbering, use stable headings, and avoid claims that tests cannot support.

## Make experiments reproducible

Put experimental implementations behind protocols, configuration, or
dependency injection. Record model names, hardware, software versions,
parameters, dataset/sample identity, and measurement commands. Keep benchmark
outputs out of source files unless they are small and intentional; store large
artifacts separately and link to them. A useful experiment should be easy to
rerun and easy to compare with the baseline.

## The review loop for every module

1. Read the current docs and inspect the existing boundaries.
2. State a narrow change and its invariants.
3. Implement the smallest complete slice.
4. Add tests for behavior, limits, and failure paths.
5. Run focused tests, then the broader suite and formatter/linter.
6. Review the diff for accidental files, secrets, migrations, and coupling.
7. Update Markdown with the new contract and a runnable example.
8. Commit a coherent change with a message describing the outcome.

This loop is deliberately compatible with future optimization work: a CUDA
kernel, batching strategy, larger model, or serving runtime can be introduced
as one experiment while the API contract and baseline remain measurable.
