# EdgentRAG v2

The same product as version 1, and the **same deployment** — one EC2 machine,
one Cloudflare tunnel, SQLite on local disk. What changed is the code: the API
is async and does no slow work, and background jobs go through SQS to separate
worker processes instead of `background.add_task`.

`DEPLOY.md` is the step-by-step deployment runbook. `TUTORIAL.md` explains why
the code is shaped this way, in order. `V3_DESIGN.html` is the step *after* this
one — managed infrastructure, for when a single machine runs out.

**The three model services are unchanged.** They still run on Colab, from
`../v1/backend/services`, and you still paste their three addresses into the
app's first screen. Everything in this directory is the AWS side.

---

## The one rule

**The API never does slow work.** It validates a request, writes a row, puts a
message on a queue, and returns. Anything measured in seconds happens in a
worker, in a process whose death costs nothing.

Every difference from version 1 follows from that.

---

## What is here

```
backend/
  shared/        config, models, db, storage, queues, events, clients, chunking
  api/           FastAPI, fully async — routes and the event stream
  workers/       ingest.py and chat.py, plus the consume loop they share
docker/          three Dockerfiles + the nginx config
scripts/         bootstrap.py — creates the two SQS queues
frontend/        version 1's UI, with polling replaced by an event stream
docker-compose.yml
deploy.sh
```

Four processes, three images — `web` (nginx + the built frontend), and one
Python image that runs as the API or as either worker depending on the command.

---

## Running it

Same deployment as version 1 — one machine, one Cloudflare tunnel, SQLite on
local disk. What changed is the code behind it.

```bash
cp .env.example .env          # fill in S3_BUCKET and the two queue URLs
python -m scripts.bootstrap   # once, prints the queue URLs
docker compose up -d --build
make url                      # the https address the tunnel handed out
```

Six containers: `web` (nginx), `api`, `ingest-worker`, `chat-worker`, `redis`,
`cloudflared`. Only `web` is reachable from outside; the API listens on the
compose network alone.

### Pointing it at Colab

Start the services on Colab exactly as before, then paste the three addresses
into the app's first screen. They are stored in Redis, so every process sees
the change immediately — no restart, no redeploy.

### Watching it work

```bash
make queues                        # depth and in-flight counts
make scale-ingest N=3              # more consumers, same queue
docker compose logs -f ingest-worker
```

Scaling the workers while a large upload is processing is the most direct way
to see what version 2 is for. So is killing one mid-job: the file is
reprocessed and nothing is lost, because the handler is idempotent.

### Deploying a change

```bash
./deploy.sh ubuntu@<ip> ~/.ssh/key.pem     # from your laptop
# then on the box:
docker compose up -d --build
```

Or, with the code in git, skip the copy entirely:

```bash
ssh <host> 'cd edgentrag-v2 && git pull && docker compose up -d --build'
```

---

## What changed from version 1

| | v1 | v2 |
|---|---|---|
| background work | `background.add_task`, in the API | SQS + separate worker fleets |
| database | SQLite, writers block readers | SQLite in **WAL mode**, shared by four processes |
| chunk text | a `.jsonl` file in storage | a row in the database |
| conversation window | a Python dict | Redis |
| progress | poll every 2 seconds | server-sent events over Redis pub/sub |
| API handlers | `def`, on a 40-thread pool | `async def`, on the event loop |
| chunking | whole document in memory | streamed |
| calls to the GPU | no timeout, no retry | timeout, backoff, circuit breaker |
| deployment | rsync onto one instance | *unchanged* — same box, same tunnel |

### The pieces that did not change

The prompt assembly, the retrieval flow, the presigned-upload pattern, the
service contracts, and almost all of the frontend. Version 1 put slow work in
the wrong *place*; it did not write it wrongly. That most of it survived the
move is a good sign about how it was structured.

---

## The one change the services would need

Chunk **text** lives in the database now, which removes version 1's round trip to
object storage on every question. Chunk **vectors** still live in Chroma beside
the embedding service, because that service stores them itself and offers no way
to hand them back.

If it grew one — `/embed` returning the vectors it computed — then:

- vectors would move into the database with `pgvector`, alongside the text
- a chunk and its vector would be written in one transaction, so they could not
  disagree
- retrieval would become a single SQL query instead of a network call
- the index would survive the Colab runtime being recycled

That is the only place where the AWS side is held back by the GPU side, and it
is a small change. It is out of scope here because the brief was to leave the
services alone.

Until then: **re-uploading rebuilds the index**, and nothing else is lost,
because the text is in the database.

---

## Things worth knowing

**SQLite works here because of WAL, and only on one machine.** Version 1 ran it
in the default journal mode, where a writer blocks every reader. WAL lets one
writer and many readers proceed together, and lets several processes share the
file — but only on the same host, because WAL does not work over a network
filesystem. That is the exact wall that makes a managed database necessary
later, and why this one stays behind a URL.

**At-least-once delivery.** A message can arrive twice — a worker that dies
after finishing but before acknowledging will have its message redelivered. Both
handlers are idempotent: chunk keys are deterministic and written with an
upsert, and a message already marked `done` is skipped.

**Workers extend their own visibility.** A job taking longer than the queue's
timeout would otherwise be handed to a second worker while the first is still
on it.

**Graceful shutdown.** `SIGTERM` stops the worker pulling new messages and lets
the current one finish, so a deploy does not duplicate in-flight work. Compose
allows 30 seconds for that by default; `docker compose stop -t 60` gives more.

**The database is the record; Redis is the live stream.** If nobody is watching
an event, it is dropped — and that is fine, because the finished answer is
written to the database and a reconnecting browser can fetch it.

**`/health` and `/ready` are different on purpose.** The load balancer checks
`/health`, which touches nothing external. If it tested the database, a brief
blip would fail every task at once and the balancer would remove all of them —
turning a recoverable problem into an outage.

---

## What AWS is actually used for

Two services, both effectively free at this size:

**S3** — the same bucket as version 1. Browsers upload straight to it; the
speech-to-text service downloads straight from it. File bytes never pass
through the application.

**SQS** — two queues plus their dead-letter queues. The free tier is a million
requests a month, permanently.

No RDS, no ElastiCache, no load balancer, no container orchestrator — the
deployment is version 1's, unchanged. Credentials come from the instance's IAM
role, so there is no access key anywhere.

---

## What version 2 still does not solve

**The GPU is the ceiling**, and in this course it is a single Colab runtime with
no autoscaling. Expect an indexing backlog under load. The queue makes that a
wait rather than a failure, which is the most an architecture can do about a
hardware limit.

**One machine is still one machine.** Workers scale to the box's memory and no
further, because WAL mode requires them to share a local disk. Past that point
you need a managed database and a load balancer — a configuration change rather
than a rewrite, but a real cost.

**Retrieval quality is untouched.** Chunking strategy, re-ranking and prompt
assembly decide that, and they are identical in both versions. It is also where
the most headroom is left.
