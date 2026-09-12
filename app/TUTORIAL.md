# EdgentRAG v2 — the version that survives

**Written for someone who has built and run version 1.** Everything that is the
same — what RAG is, why we chunk, how presigned URLs work, what the three model
services do — is not repeated here. This is about what changed, why, and the
AWS concepts that arrive with it.

If a term already appeared in the version 1 tutorial, it is assumed. New ones
get a two-line box the first time they turn up.

---

## Contents

**The shape of the change**

1. [One rule, and everything that follows](#1-one-rule-and-everything-that-follows)
2. [The code, file by file](#2-the-code-file-by-file)
3. [Tracing a request through the new topology](#3-tracing-a-request-through-the-new-topology)

**The new concepts**

4. [Queues, properly](#4-queues-properly)
5. [The broker: how the GPU gets work](#5-the-broker-how-the-gpu-gets-work)
6. [Idempotency, and why it is not optional](#6-idempotency-and-why-it-is-not-optional)
7. [SQLite, and the one setting that changes everything](#7-sqlite-and-the-one-setting-that-changes-everything)
8. [Redis: a cache and a message bus](#8-redis-a-cache-and-a-message-bus)
9. [Server-sent events](#9-server-sent-events)
10. [Containers, and why there are three images](#10-containers-and-why-there-are-three-images)
11. [Scaling on one machine](#11-scaling-on-one-machine)

**Running and operating it**

12. [Running it](#12-running-it)
13. [Deploying](#13-deploying)
14. [Three bugs this design made possible](#14-three-bugs-this-design-made-possible)
15. [What version 2 still does not solve](#15-what-version-2-still-does-not-solve)
16. [Glossary of new terms](#16-glossary-of-new-terms)

---

## 1. One rule, and everything that follows

Version 1's fifteen documented limitations mostly trace back to a single line:

```python
background.add_task(ingest.run, session_id)
```

That ran ingestion **inside the web server**, on the same forty-thread pool that
served requests. From that one decision came: work lost on restart, no retries,
one person's upload slowing everyone's chat, and forty concurrent operations
freezing the API for people who were not doing anything.

Version 2 reverses it:

> **The API never does slow work.** It validates a request, writes a row, puts a
> message on a queue, and returns. Anything measured in seconds happens
> somewhere else, in a process whose death costs nothing.

Everything below is a consequence of that sentence.

### The tiers

```
   browser
      │  https
      ▼
   cloudflared ──► nginx ──┬── /       the built frontend
                           └── /api ──► API  (async; writes a row, enqueues, returns)
                                          │
                                        SQS ──┬──► ingest workers ──► SQLite · S3 · GPU
                                              └──► chat workers   ──► SQLite · Redis · GPU
```

Four programs where version 1 had one, all on the same box. They share a
database file, a cache and a bucket; they share no memory, no threads, and no
fate.

**The deployment is deliberately unchanged from version 1** — one machine, one
tunnel, SQLite on local disk. What changed is the code.

### What did not change

The prompt assembly, the retrieval flow, the chunking strategy, the presigned
upload pattern, the service contracts, and almost all of the frontend. Version 1
put slow work in the wrong *place*; it did not write it wrongly. That most of the
code survived the move is the most useful thing to notice about it.

---

## 2. The code, file by file

5,192 lines of Python. On the EC2 box: four processes built from three images.
On Colab: the same three services as version 1, now pulling their own work.

### `backend/shared/` — what all three import

| file | lines | what it does | new? |
|---|---|---|---|
| `config.py` | 99 | every setting for all three programs | |
| `models.py` | 169 | four tables — `chunks` is the new one | changed |
| `db.py` | 144 | **two** engines: async for the API, sync for workers | **new** |
| `storage.py` | 145 | S3 only; the local-disk implementation is gone | changed |
| `queues.py` | 143 | send, receive, delete, extend, release, depth | **new** |
| `events.py` | 86 | Redis: conversation window **and** pub/sub | **new** |
| `services.py` | 68 | where the Colab services are, kept in Redis | changed |
| `clients.py` | 163 | the **two** GPU calls somebody waits for, with timeout, retry, circuit breaker | changed |
| `chunking.py` | 111 | streaming chunker — an iterator, not a list | changed |
| `convert.py` | 78 | file → text **file**, not text string | changed |
| `bookkeeping.py` | 104 | when a file, and then a session, is finished | **new** |
| `schemas.py` | 145 | request and response shapes, including the broker's | |

### `backend/api/` — thin and async

| file | lines | notes |
|---|---|---|
| `main.py` | 68 | wires routers; no business logic |
| `routes/sessions.py` | 57 | create; status |
| `routes/uploads.py` | 120 | presign; **enqueue one message per file** |
| `routes/chat.py` | 70 | two rows, one message, `202` |
| `routes/events.py` | 115 | **the SSE stream** — the replacement for polling |
| `routes/health.py` | 93 | `/health`, `/ready`, `/metrics/queues` |
| `routes/config.py` | 83 | the three service addresses |
| `routes/broker.py` | 275 | **claim / heartbeat / complete** — the GPU's only way in |

Every handler is `async def`. That is a promise the whole file keeps: the
database is `aiosqlite`, Redis is `redis.asyncio`, and the one library that is
only synchronous — boto3, for signing and queueing — is pushed to a thread with
`run_in_threadpool`.

### `backend/workers/` — where the work went

| file | lines | notes |
|---|---|---|
| `base.py` | 140 | the consume loop both share: poll, handle, heartbeat, acknowledge |
| `ingest.py` | 282 | one message = one *step* of a file |
| `chat.py` | 214 | retrieve, prompt, generate, publish, save |

### `services/` — the three model services, on Colab

They are in this directory now, because their communication changed even though
their models did not.

| file | lines | notes |
|---|---|---|
| `broker.py` | 237 | **new** — claim, heartbeat, complete, and the presigned-URL helpers |
| `stt/jobs.py` | 65 | **new** — transcription as a pulled job |
| `embedding/jobs.py` | 65 | **new** — indexing as a pulled job |
| `embedding/app.py` | 154 | `/retrieve` unchanged; now also starts a poller |
| `stt/app.py` | 188 | `/transcribe` kept for testing; now also starts a poller |
| `llm/app.py` | 79 | untouched — generation is always a direct call |

Nothing about the *models* changed: same weights, same batch sizes, same
Chroma. What changed is how the work reaches them.

### And the rest

```
scripts/bootstrap.py   creates the four SQS queues and their dead-letter queues
docker/                three Dockerfiles + the nginx config
docker-compose.yml     the whole application: web, api, two workers, redis, tunnel
deploy.sh              rsync to the box, same as version 1
DEPLOY.md              the deployment runbook, step by step
```

---

## 3. Tracing a request through the new topology

Do this once for an upload and once for a question, exactly as you did in
version 1. The files are different; the technique is not.

### Trace A — a document is uploaded

| # | where | what happens |
|---|---|---|
| 1 | `api/routes/uploads.py` | `File` rows created, presigned PUTs returned |
| 2 | *the browser* | uploads **straight to S3** — unchanged from v1 |
| 3 | `api/routes/uploads.py:register` | session → `processing`, **one SQS message per file**, `200` |
| — | | *the API's involvement ends here* |
| 4 | `workers/base.py` | a worker long-polls, receives, starts a handler thread |
| 5 | `workers/ingest.py:handle` | file → `processing`, publishes a progress event |
| 6 | `_prepare_document` | downloads to disk, converts to a **file**, streams chunks |
| 7 | `_store_and_queue` | upserts chunk text into SQLite, writes a `.jsonl` to S3, **queues an embed job**, stops |
| — | | *the worker's involvement ends here too* |
| 8 | *the GPU* | claims the job from `/broker/claim`, reads the `.jsonl`, embeds it |
| 9 | `api/routes/broker.py:complete` | file → `done`, session recounted |
| 10 | `shared/events.py:publish` | a Redis message |
| 11 | `api/routes/events.py` | whichever API task holds the browser's stream relays it |

Compare step 3 with version 1's `background.add_task(...)`. That is the entire
architectural change, in one line.

Notice how many times the chain **stops**. The API stops at step 3. The worker
stops at step 7. Nothing is holding a connection open waiting for the step
after it, which is why a twenty-minute job costs no process anything.

### Trace A′ — the same, but it is a video

| # | where | what happens |
|---|---|---|
| 6 | `_request_transcription` | **queues an stt job** with two signed URLs, stops |
| 7 | *the GPU* | claims it, downloads the video from S3, transcribes, writes the transcript to S3 |
| 8 | `broker.py:complete` | puts the file **back** on the ingest queue, marked `stage: transcribed` |
| 9 | `_chunk_transcript` | a worker reads the transcript, stores chunks, queues an embed job |
| 10 | *the GPU* | indexes; the file is done |

The same file passes through the ingest queue **twice**. That is what makes it
a state machine rather than a procedure: each message does one short step and
schedules the next.

### Trace B — a question is asked

| # | where | what happens |
|---|---|---|
| 1 | `api/routes/chat.py` | two `Message` rows, one SQS message, `202` |
| 2 | `workers/chat.py:handle` | message → `answering`, publishes `retrieving` |
| 3 | `clients.retrieve` | the embedding service searches and returns **keys and scores** |
| 4 | `_fetch_sources` | **one SQL query** gets the text — v1 read a `.jsonl` from S3 per file |
| 5 | `build_prompt` | unchanged from v1 |
| 6 | `clients.generate` | a direct call, not a queue |
| 7 | `publish_token` | to Redis, for the stream |
| 8 | | the finished answer is written to SQLite |

Two things worth stopping on. **Step 4** is why chunk text moved into the
database: retrieval no longer makes a network round trip to object storage.
**Step 7 before step 8** is deliberate — the stream is for speed, the database
is for truth, and a browser that was not connected can still fetch the answer.

And notice **steps 3 and 6 are direct HTTP calls**, where trace A queues
everything. That contrast is the subject of section 5, and it comes down to one
question: is a person sitting there watching a spinner?

---

## 4. Queues, properly

This is the concept version 2 exists for. Everything else is a consequence.

> **Message queue**
> A durable list of work. A producer writes; a consumer reads. The message is
> not removed when it is read — only when the consumer says it is finished.

That last clause is the whole thing. It is what makes a worker's death free.

### The lifecycle

```
send ──► visible ──► received ──► invisible (the worker owns it)
                                        │
                        acknowledged ───┼──► deleted, gone
                        timed out    ───┴──► visible again, someone else takes it
```

> **Visibility timeout**
> How long a received message stays hidden. If the consumer has not
> acknowledged by then, the message comes back. Ours is 900 seconds — at least
> as long as the slowest job.

A worker killed mid-transcription costs one redelivery. Version 1's equivalent —
a deploy during ingestion — destroyed the work with no record that it had
existed.

### Long polling

```python
client.receive_message(..., WaitTimeSeconds=20)
```

> **Long polling**
> The receive call waits up to twenty seconds for work rather than returning
> empty immediately. One call instead of hundreds, and it returns the instant a
> message arrives.

Short polling in a loop is both slower to react and dramatically more expensive,
because you pay per request.

### Dead-letter queues

> **Dead-letter queue (DLQ)**
> Where a message goes after failing too many times. Ours: five attempts.

Without one, a message that can never succeed is retried for ever, occupying a
worker each time. With one it moves aside and becomes a number you can alarm on.
**Alarm on both DLQs** — a message arriving there means something failed five
times and nothing else will tell you.

### Two queues, not one

```
ingest   bulk work,        one message per file,      throughput matters
chat     interactive work, one message per question,  latency matters
```

Sharing one would put a fifty-file upload in front of everybody's questions —
version 1's limit 02 reproduced at a larger scale. Separate queues mean separate
worker fleets, which mean separate scaling.

### The heartbeat

`workers/base.py` runs the handler on a thread and extends the message's
visibility while it works:

```python
while not done.wait(timeout=self.heartbeat_seconds):
    queues.extend(message, settings.queue_visibility_seconds)
```

Without this, a job outliving the visibility timeout is handed to a *second*
worker while the first is still on it — and now two workers are transcribing the
same video.

### Graceful shutdown

A container gets `SIGTERM`, then `SIGKILL` some seconds later. The worker stops
pulling new messages, finishes the one in hand, and hands any prefetched ones
straight back:

```python
if self._stopping.is_set():
    self._release(messages[index:])   # visible immediately, not in 900 seconds
    break
```

ECS's `stopTimeout` is set to match. Without the pause, every deploy duplicates
whatever was in flight.

---

## 5. The broker: how the GPU gets work

Section 4 said everything slow belongs on a queue. The slowest thing this
system does is transcribe a video, and the second slowest is index a document.
Both happen on a GPU that is not ours — a Colab runtime.

So: put them on a queue. And immediately run into a problem.

### The problem, stated plainly

To read from an SQS queue you need AWS credentials. The Colab runtime cannot
have any.

It is worth being precise about *why*, because "just give it a key" is the
obvious answer and it is wrong:

- Our EC2 box gets credentials from an **instance role** — AWS knows which
  machine it is and hands it temporary keys that rotate by themselves. Colab is
  not our machine and AWS has never heard of it, so there is nothing to
  recognise.
- We could create an access key and paste it into a file on Drive. That key
  does not expire, sits in a folder that is shared by a link, and would be
  copied into every student's runtime. It is a permanent credential in the
  least private place available.

That is the whole dilemma: **the work belongs on a queue, and the machine that
does the work cannot be allowed near the queue.**

### The pattern

Put something in between. The GPU asks *us* for work; we talk to the queue on
its behalf.

```
   ingest worker ──► SQS ──► [ broker ] ◄────────── GPU
                    (we hold                 "any work for me?"
                  the credentials)           "here you go"
                                             "still going..."
                                             "done"
```

That middle box is three endpoints on our own API, in
`backend/api/routes/broker.py`:

| endpoint | the GPU is saying | what we do |
|---|---|---|
| `POST /broker/claim` | "anything to do?" | `ReceiveMessage` on SQS; hand back the job, or 204 |
| `POST /broker/heartbeat` | "still going" | `ChangeMessageVisibility` — keep the job theirs |
| `POST /broker/complete` | "done" / "failed" | `DeleteMessage`, or leave it to be retried |

The GPU holds **one token**. Not an AWS key — a random string we generated,
sent as `Authorization: Bearer …`. What can someone do with a stolen copy? Ask
for a job. That is the entire blast radius. They cannot read the bucket, list
the queues, see another session, or reach anything else in the account. And
revoking it is one line in `.env` plus a restart.

Compare that with an access key, where the honest answer to "what can they do
with it?" is "whatever its policy allows, until somebody notices."

### But how does the GPU read the video?

It has no bucket access either. So the job it receives carries **presigned
URLs** — already in the message:

```json
{
  "session_id": "…", "file_id": "…", "filename": "lecture.mp4",
  "media_url":  "https://…s3….amazonaws.com/…?X-Amz-Signature=…",
  "result_url": "https://…s3….amazonaws.com/…?X-Amz-Signature=…"
}
```

> **Presigned URL**
> An ordinary https address with a signature stuck on the end that means:
> *whoever holds this link may do this one thing, to this one object, until
> this time.* No login, no key.

One link to read the video, one to write the transcript back. The GPU uses
plain `requests.get` and `requests.put` and never learns the bucket's name.
Two doors, both of which lock themselves.

**One detail that is easy to get wrong:** how long those links last. The
obvious choice is an hour. But a job may sit behind two hours of backlog before
the GPU reaches it, and a link that expired while it waited fails in a way that
looks like a permissions bug. So `job_url_expiry_seconds` is six hours — the
expiry has to outlive the *queue*, not the job.

### What this buys, beyond the credential

**The GPU needs no address of its own.** Every connection starts at the GPU and
goes outward. Colab allows that freely. Nothing has to reach *in*, which means
no tunnel, no hostname that changes on every restart, and none of version 1's
hundred-second proxy limit — for this half of the system.

**Crashes stop costing work.** Under the old design the backend called
`/transcribe` and held that connection for twenty minutes. If the runtime died
at minute nineteen, the work was gone and nobody had written down that it had
ever started. Now the job is only acknowledged at `complete`. A runtime that
dies mid-transcription loses nothing: the message becomes visible again and is
handed out afresh.

**A crowd becomes a queue.** Fifty uploads at once used to mean fifty
simultaneous connections to one GPU. Now it means a backlog with a number you
can read (`make queues`).

### When the GPU fails

`complete` can say `ok: false`, and what happens next is worth walking through
because two of the three behaviours are easy to get wrong.

**It is not acknowledged.** The message stays on the queue. That is the whole
point of putting it there.

**It is not retried immediately.** The obvious implementation makes the message
visible again at once, and it is wrong: a job that fails in two seconds — a
CUDA out-of-memory, say — would be reclaimed instantly, fail again, and burn
all five attempts in about ten seconds. Nothing transient ever gets a chance to
clear. So the retry is hidden for thirty seconds, then sixty, then a hundred
and twenty, capped at the visibility timeout.

**The last attempt gives up on purpose.** After five deliveries SQS moves the
message to the dead-letter queue and stops caring. If the broker also stopped
caring, the file would sit at `processing` for ever and its session would never
close — the browser would spin with no error and nothing to explain it. So the
broker recognises the last attempt, marks the file failed with the reason, and
settles the session.

That last one is the bug that is easy to *ship*, because everything works until
something fails five times, and then it hangs rather than erroring.

### Where the GPU still gets *called*

Two endpoints stay direct HTTP, and this is the distinction worth teaching:

| | how it arrives | why |
|---|---|---|
| transcription | queued job, pulled | minutes; nobody waiting |
| indexing | queued job, pulled | minutes; nobody waiting |
| `/retrieve` | direct call | milliseconds; **somebody is waiting** |
| `/generate` | direct call | seconds; **somebody is waiting** |

The rule is not "queues are better." It is: **queue the work nobody is waiting
for, and call the work somebody is.** A queue costs you a poll interval and a
round trip, which is free when a job takes twenty minutes and unacceptable when
a person is watching a cursor blink.

Those two direct calls are why the Colab tunnel still exists at all, and why
`shared/clients.py` still has retries, backoff and a circuit breaker — that
file now contains *only* the calls where somebody is waiting.

### The GPU side

`services/broker.py` is the whole client, and it is about a hundred lines. The
loop is:

```python
while running:
    offer = client.claim("stt")      # blocks up to 20s, then returns nothing
    if offer is None:
        continue
    run the handler on another thread
    heartbeat every 30s while it runs
    client.complete(lease, ok=True/False)
```

Two things about it are worth pointing at.

**It runs inside the existing service, on a thread** — not as a fourth program.
The model is already loaded in that process, and a separate program would mean
a second copy of the weights on the same card.

That does mean the embedding service now does two things at once: a poller
thread indexing a batch, and a request thread answering `/retrieve`. They share
one model and one Chroma store. It works, and on one card the GPU serialises
them anyway — but it is worth knowing that a large indexing job makes questions
feel slower, because there is exactly one card and it is busy.

**The heartbeat is the same shape as `workers/base.py`.** Both hold a claim on
a message while a slow job runs, and both do it by handing the work to another
thread so the main one stays free to say "still mine." When you see the same
shape twice in a codebase, it is usually the problem's shape rather than a
coincidence — here the problem is "a lease that must be renewed."

### The state machine this creates

Because nothing waits, ingesting a video is now four short steps that schedule
each other:

```
ingest queue ──► worker: queue an stt job, stop
   stt queue ──► GPU: transcribe, write to S3, report
                      broker: put it back on the ingest queue
ingest queue ──► worker: chunk the transcript, queue an embed job, stop
 embed queue ──► GPU: index, report
                      broker: mark the file done
```

The same file passes through the ingest queue twice, distinguished by a
`stage` field in the message. This is the part students find strangest and it
is the most transferable idea in the whole project: **a long job becomes a
series of short ones, each of which ends by scheduling the next.** No process
is ever blocked, so no process's death is ever expensive.

---

## 6. Idempotency, and why it is not optional

> **At-least-once delivery**
> SQS guarantees a message arrives *at least* once — never zero times, sometimes
> twice. A worker that finishes and dies before acknowledging will see its
> message again.

You cannot avoid this. You can only make a repeat harmless.

> **Idempotent**
> Doing it twice has the same effect as doing it once.

### How this codebase manages it

**Chunk keys are deterministic.** `{file_id}:{ordinal:04d}` — the same file
always produces the same keys, so a second attempt writes the same rows.

**The write is an upsert.**

```sql
INSERT INTO chunks (...) VALUES (...)
ON CONFLICT ON CONSTRAINT uq_chunks_session_key DO UPDATE SET text = EXCLUDED.text
```

An `INSERT` would fail on the retry; the upsert updates. The unique constraint
on `(session_id, chunk_key)` is what makes it possible.

**Finished work is skipped.** A file already `done`, or a message already
answered, returns immediately.

**The session counter is derived, not incremented.** This one is subtle enough
to have been a bug — see [section 14](#14-three-bugs-this-design-made-possible).

### The ordering rule

`_store_and_index` writes the text to the database **before** calling the
embedding service.
That order is deliberate:

- **Text first, then vector:** a failure leaves text with no vector. Retrieval
  never returns it. Harmless.
- **Vector first, then text:** a failure leaves a vector pointing at text that
  does not exist. Retrieval returns a hit that cannot be resolved — which is
  exactly the silent failure version 1 could produce.

When two writes cannot be atomic, order them so that the failure mode is the
harmless one.

---

## 7. SQLite, and the one setting that changes everything

Version 2 still uses SQLite — the same file, on the same box. What changed is
one setting, and it is worth understanding why that was enough.

**Version 1's problem was the journal mode, not SQLite.** In the default mode a
writer locks the whole file and **blocks every reader**. Ingestion wrote
constantly, browsers polled constantly, and they fought — which is how you get
`database is locked` after a thirty-second wait.

> **WAL (write-ahead log)**
> Writes go to a separate log file and are folded back in later. One writer and
> many readers proceed at the same time, and several *processes* can share the
> file safely.

That is the whole fix, and `db.py` applies it on every connection:

```python
PRAGMA journal_mode=WAL
PRAGMA busy_timeout=30000     # wait your turn instead of failing
PRAGMA foreign_keys=ON        # SQLite does not enforce these by default
```

Writers still take turns, but each write here is milliseconds. Measured on this
schema: **four concurrent writers, a hundred rows, zero locking errors.**

### Where the wall actually is

**WAL requires every process to be on the same machine.** It does not work over
a network filesystem. So this scales to more workers on one box — which is what
`docker compose up --scale ingest-worker=3` does — and no further.

That is the honest limit, and it is worth naming precisely: not "SQLite is a
toy", but *"SQLite is fine until you need a second machine."* When that day
comes, `DATABASE_URL` changes and nothing else does — which is why the database
stays behind a URL.

> **RDS**
> AWS's managed Postgres, for that day: backups, patching, and failover, all
> handled. It costs money, which is why it is not here.

### Two engines, one URL

`shared/db.py` builds both:

```python
_async_engine = create_async_engine(_driver(url, "aiosqlite"))  # the API
_sync_engine  = create_engine(_driver(url, None))               # the workers
```

The API is async because it holds many mostly-idle connections. The workers are
sync because blocking is their job and sync SQLAlchemy is simpler to reason
about. One `DATABASE_URL` configures both; `_driver` rewrites the scheme, and
already handles Postgres, so switching later is genuinely one line.

### The new table

`chunks` holds the text of every passage. Version 1 spread a chunk across a
`.jsonl` file in S3 and a vector in Chroma, so answering a question meant asking
Chroma which chunks won and then reading a file out of storage to find out what
was in them. Now the text is a row and retrieval is one indexed query.

**The vectors are still in Chroma**, beside the embedding service, because that
service stores them itself and offers no way to hand them back. Re-uploading
rebuilds the index, and nothing else is lost, because the text is here.

---

## 8. Redis: a cache and a message bus

Two jobs, and the second is the one that makes streaming possible.

**The conversation window.** Version 1 kept the last few turns in a Python
dictionary, which worked because there was one process. With many API tasks and
many workers, it has to live somewhere all of them can see.

**Pub/sub.** This is the interesting one.

> **Publish/subscribe**
> A publisher sends to a named channel; every current subscriber receives it.
> Nobody listening? The message is dropped.

The worker generating an answer and the API task holding the browser's
connection are **different processes and always will be**. So:

```
chat worker ──publish──► channel "events:{session_id}" ──► the API task
                                                            that is subscribed
                                                                 │
                                                                 ▼
                                                            the browser
```

Without that indirection, streaming works only when you have exactly one API
instance — which is to say, it does not work.

**Dropping events is correct.** The database still receives everything that
matters, so a browser that was not connected can fetch the result. Redis carries
the live stream; the database remains the record.

Redis also holds the three Colab service addresses, so a worker picking up a
message reads the same value the browser set a moment ago — no restart, no
redeploy.

---

## 9. Server-sent events

> **Server-sent events (SSE)**
> One long-lived HTTP response that the server keeps writing to. The browser's
> `EventSource` reads it and reconnects automatically if it drops.

Version 1's browser asked "is it done yet?" every two seconds. With three
hundred users that is a hundred and fifty requests a second carrying no
information most of the time — and the user still waited up to two seconds after
the answer already existed.

### Why SSE rather than WebSockets

The traffic is one-directional. The browser has nothing to say mid-answer. SSE
is ordinary HTTP, needs no protocol upgrade, and the reconnection logic is in
the browser already.

### Why the endpoint must be async

It holds a connection open for as long as someone is on the page, doing nothing
almost all of that time. On an event loop, hundreds cost almost nothing. On
version 1's forty-thread pool, four hundred idle users would take every thread
and the API would stop answering anything at all.

This is the one case where threads genuinely cannot substitute for async.

### The heartbeat, and what it is really for

```python
yield ": keepalive\n\n"    # a comment line; the browser ignores it
```

Recall from the version 1 tutorial that a proxy's limit is **time to first
byte**. Sending headers immediately clears that gate — but an idle stream still
gets closed by intermediaries. Early headers turn *"must finish within a hundred
seconds"* into *"must not go quiet for too long"*. Much easier, still a rule.

### Two settings that are easy to miss

**`X-Accel-Buffering: no`** — nginx buffers responses by default, which would
hold the stream until it completed and defeat the entire mechanism.

**`proxy_read_timeout`** in nginx must exceed the heartbeat interval, or nginx
closes a stream that is merely quiet. `docker/nginx.conf` sets it to 24 hours on
the events route alone; the heartbeat is every 15 seconds, so the two never
meet. The Cloudflare tunnel needs nothing: its limit is on the *first* byte, and
the `open` frame is sent immediately.

---

## 10. Containers, and why there are three images

> **Image vs container**
> An image is a filesystem plus a default command — built once, immutable. A
> container is a running instance of one. You build an image and run many
> containers from it.

> **Layer**
> Each instruction in a Dockerfile produces a layer, and layers are cached. Put
> the things that change rarely first.

That is why dependencies are installed before the code is copied:

```dockerfile
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r backend/requirements.txt      # cached unless requirements change
COPY backend /app/backend                        # this layer rebuilds on every edit
```

Reverse those two and every one-character code change reinstalls everything.

### Three images, four processes

| image | contains | size |
|---|---|---|
| `web` | nginx + the built frontend | ~25 MB |
| `api` | Python, no Docling | ~300 MB |
| `worker` | Python **and** Docling's layout and OCR models | ~1 GB |

`web.Dockerfile` is a **multi-stage build**: the first stage has Node and builds
the bundle, the second is nginx and the bundle alone. Node never reaches the
finished image, which is the difference between 25 MB and 400.

Both workers run from the **same** image. Which one starts is decided by the
command:

```yaml
command: ["python", "-m", "backend.workers.ingest"]
command: ["python", "-m", "backend.workers.chat"]
```

Two images that differed only in an entrypoint would be two things to build and
keep in step.

### Two details worth copying

**Do not run as root.** Both images create an unprivileged user. If a container
is compromised, this is the difference between an inconvenience and a bad day.

**The API's healthcheck touches nothing external.** If it tested the database, a
brief blip would fail every task at once and the load balancer would remove all
of them — turning a recoverable problem into a total outage. That is why
`/health` and `/ready` are different endpoints.

### Real AWS, not a simulator

S3 and SQS are the real services, in development and in production. Both are
effectively free at this size — SQS gives a million requests a month,
permanently — and credentials come from the instance's IAM role, so no key
exists anywhere.

The consequence worth noticing: **there is no "local mode" branch anywhere in
version 2.** No `if settings.is_local`, no second storage implementation.
Version 1 had two storage backends precisely because one of its two worlds was
a folder on disk.

---

## 11. Scaling on one machine

Version 2 scales, and it does so without any managed service. The unit is a
container, and the command is one line:

```bash
docker compose up -d --scale ingest-worker=3
```

Three consumers on the same queue. SQS hands each message to exactly one of
them, so they divide the work without coordinating — no locks, no leader, no
list of who has what. That property is the queue's, not yours.

### What can and cannot scale this way

| | scales | why |
|---|---|---|
| `ingest-worker` | **yes**, freely | independent messages, idempotent handler |
| `chat-worker` | **yes**, freely | same |
| `api` | not usefully | one nginx, one upstream; it is not the bottleneck anyway |
| `redis`, `web` | no | one of each is the design |

The database is the interesting constraint. SQLite in WAL mode is safe for
several processes **on one machine**, so scaling workers works — right up to
the point where you want a second machine, and then it does not, because WAL
does not work over a network filesystem.

### The signal to scale on

```bash
make queues       # {"ingest": {"waiting": 128, "in_flight": 3}, ...}
```

**Waiting** is the backlog. **In-flight** is what workers currently hold. If
waiting keeps climbing while in-flight is flat, you need more consumers. If
waiting is zero, you do not.

This is the number a managed autoscaler would read, and looking at it by hand
first is the better way to learn what it means. Worth knowing for later:
production systems scale on queue *depth* but **alarm on queue age** — the age
of the oldest message is the only number that corresponds to how long a real
person has waited, because depth means something different depending on how
large the documents are.

### Where this runs out

Three limits, in the order you would meet them:

**The GPU**, first and hardest. Every improvement here delivers work to the
model services faster. One Colab runtime transcribes one video at a time, and
no amount of scaling on this side changes that. The queue turns the excess into
a backlog rather than a cascade of timeouts — which is the most an architecture
can do about a hardware limit.

**SQLite**, when you want a second machine.

**The machine itself**, when the workers no longer fit in its memory.

At that point the answer is a load balancer, a container orchestrator and a
managed database — and every one of those is a configuration change rather than
a rewrite, because the code already talks to a queue, a URL, and a cache.

---

## 12. Running it

```bash
cp .env.example .env          # S3_BUCKET and the two queue URLs
python -m scripts.bootstrap   # once — creates the queues, prints their URLs
docker compose up -d --build
make url                      # the https address the tunnel handed out
```

Six containers: `web`, `api`, `ingest-worker`, `chat-worker`, `redis`,
`cloudflared`. Only `web` is reachable from outside — the API has no `ports`
entry at all, so nothing but nginx can talk to it and no security-group rule is
needed for it.

Then start the model services on Colab and paste their three addresses into the
app's first screen, exactly as in version 1.

### The exercises that teach the most

**Watch a backlog drain.**

```bash
make queues                        # waiting and in-flight
make scale-ingest N=3
```

Upload several files and watch the numbers move. Version 1 could not do this at
all — there was nothing to scale and no number to watch.

**Kill a worker mid-job.**

```bash
docker compose kill ingest-worker
docker compose up -d ingest-worker
```

The file is reprocessed and nothing is lost, because the message was never
acknowledged. Do the equivalent in version 1 — restart the API during
ingestion — and the work vanishes with no record it existed.

**Break the acknowledgement.** Put `raise RuntimeError("boom")` at the very end
of `ingest.handle`, after the work is done. Watch it retry five times, then land
in the dead-letter queue.

**Watch the event stream directly.**

```bash
curl -N http://localhost:8080/api/sessions/<id>/events
```

`-N` disables curl's buffering. You will see the `open` frame immediately, then
keepalive comments, then real events as workers publish them.

**Prove SQLite is coping.** While an upload is processing:

```bash
docker compose exec api python -c "
import sqlite3; print(sqlite3.connect('/data/edgentrag.db').execute('PRAGMA journal_mode').fetchone())"
```

`wal`. In version 1's mode, the readers and writers you are watching would be
blocking each other.

---

## 13. Deploying

Same as version 1 — copy the code and restart the containers.

```bash
./deploy.sh ubuntu@<ip> ~/.ssh/key.pem      # from your laptop
```

then on the box:

```bash
docker compose up -d --build
make url
```

Or, with the code in git, skip the copy:

```bash
ssh <host> 'cd edgentrag-v2 && git pull && docker compose up -d --build'
```

**What the box needs:** Docker, an IAM instance role granting S3 and SQS, and
`.env` with the bucket and queue URLs. Nothing else — no database to install,
no web server to configure, no systemd units. That is the part containers
actually bought you.

**Rebuilds are incremental.** Only the layers after a change are redone, which
is why the Dockerfiles install dependencies before copying code.

---

## 14. Three bugs this design made possible

Distributed systems introduce failure modes that a single process cannot have.
All three of these were found by review before the code ever ran, and each is a
category worth recognising.

### The counter that counted attempts

`_finish_file` runs in a `finally`, so it also runs on the failure path — right
before the message goes back to the queue. The original version did:

```python
files_done = files_done + 1        # wrong
```

A one-file session that failed once and succeeded on retry ended up claiming two
of one file done. With five attempts, six. The session status flapped and the
number was meaningless.

**The fix is the lesson:** count, do not increment.

```sql
SELECT count(*) FROM files WHERE session_id = :sid AND status IN ('done','failed')
```

Deriving a value is idempotent by construction. Incrementing one is idempotent
only if it runs exactly once — which at-least-once delivery does not promise.

### Acknowledging work that had not finished

If extending visibility failed, the heartbeat loop broke while the handler
thread was still running. `join(timeout=5)` returned anyway, no failure had been
recorded *yet*, and the code fell through to deleting the message. A transient
network blip would acknowledge work that then failed, with nothing to retry it.

**The lesson:** "no error recorded" and "finished successfully" are different
states. The fix gates the delete on completion, not on the absence of a failure.

### A URL scheme that crashed everything

`postgres://` versus `postgresql://`. One works, the other fails at *import*
with `Can't load plugin`, so every process dies on start. Local compose was
unaffected; production would never have booted.

**The lesson:** the environments differ in ways your tests do not. This one is
only reachable through a value copied from an AWS console.

---

## 15. What version 2 still does not solve

**The GPU is the ceiling**, and in this course it is a single Colab runtime with
no autoscaling. Expect an indexing backlog under load. The queue makes that a
wait rather than a failure, which is the most an architecture can do about a
hardware limit.

**One machine is still one machine.** Workers scale to the box's memory and no
further, because WAL mode requires them to share a local disk. Crossing that
line means a managed database and a load balancer — a configuration change
rather than a rewrite, but a real cost: a few hundred dollars a month before a
single user arrives.

**More moving parts, more ways to be subtly wrong.** Section 14 is three
examples, found before the code ran. There will be others, which is why
observability is not optional here in a way it was not for version 1.

**Retrieval quality is untouched.** Chunking, re-ranking and prompt assembly
decide that, and they are identical in both versions. It is also where the most
headroom is left.

**One region.** A regional outage is an outage.

---

## 16. Glossary of new terms

| term | meaning |
|---|---|
| **Bearer token** | A random string sent in a header that proves you may ask. Not an AWS credential and cannot become one. |
| **Broker** | A small endpoint of ours that reads the queue on the GPU's behalf, so the GPU needs no credentials. |
| **Presigned URL** | A link with a signature that permits one operation on one object until it expires. |
| **At-least-once delivery** | A message arrives once or more, never zero times. Handlers must tolerate repeats. |
| **Circuit breaker** | After repeated failures, calls fail immediately instead of waiting out a timeout. |
| **Dead-letter queue** | Where a message goes after failing too many times, instead of retrying for ever. |
| **Idempotent** | Doing it twice has the same effect as doing it once. |
| **Layer** | One cached step of an image build. Order instructions so the slow ones change least. |
| **Multi-stage build** | Build in one image, copy only the result into a smaller one. |
| **WAL** | SQLite's write-ahead log: one writer and many readers at the same time, on one machine. |
| **Lease** | An opaque handle naming the job a worker currently holds; renewed by heartbeat. |
| **Long polling** | A receive call that waits for work rather than returning empty. |
| **Publish/subscribe** | Send to a channel; whoever is listening receives it. Nobody listening, nothing kept. |
| **SQS** | AWS's message queue. |
| **SSE** | One long-lived HTTP response the server keeps writing to. |
| **Visibility timeout** | How long a received message stays hidden before it returns to the queue. |

---

## Where to go next

- `DEPLOY.md` — the deployment runbook, step by step
- `README.md` — how to run it, and what changed from version 1
- `V3_DESIGN.html` — the managed-infrastructure step after this one, and its cost
- `../v1/TUTORIAL.md` §12 and §13 — the fundamentals underneath all of this:
  thread pools, event loops, timeouts, and sync versus async







Listed directory python_project
Listed directory v1
Listed directory v2
Listed directory backend
Listed directory monolith
Listed directory backend
Listed directory app
Listed directory routes
Listed directory routes
Viewed uploads.py:1-134
Viewed uploads.py:1-121
Viewed chat.py:1-114
Viewed chat.py:1-71
Viewed TUTORIAL.md:1-100
Viewed db.py:1-90
Viewed events.py:1-116
Viewed main.py:1-80
Viewed main.py:1-65
Viewed health.py:1-41
Viewed health.py:1-88
Listed directory workers
Viewed base.py:1-60

Here is a detailed breakdown of the differences between **Synchronous vs. Asynchronous processing**, followed by why the route handlers were redesigned from synchronous (`def`) in **v1** to asynchronous (`async def`) in **v2**.

---
# Concepts 


### 1. Synchronous vs. Asynchronous Process: Core Concepts

| Aspect | Synchronous (Sync) | Asynchronous (Async) |
| :--- | :--- | :--- |
| **Execution Flow** | Sequential. Step 2 does not begin until Step 1 completes. | Non-blocking / Event-driven. When an I/O task starts, control yields so other tasks can run. |
| **I/O Waiting (DB, Network, Disk)** | The executing thread is **blocked** (idle/frozen) waiting for data to return. | The thread releases execution (`await`) and handles other requests while waiting for I/O. |
| **Concurrency Mechanism** | Multi-threading or multi-processing (each concurrent client requires an OS thread/process). | Single-threaded event loop (handles thousands of concurrent connections concurrently). |
| **Resource Cost** | High memory/context-switch overhead per thread (e.g. 40 threads ≈ 40 max concurrent tasks). | Very low memory footprint (lightweight coroutines instead of OS threads). |
| **Ideal For** | Heavy CPU-bound computation, sequential pipelines, simple worker loops. | High-concurrency I/O, web APIs, real-time streaming (SSE/WebSockets), microservices. |

> **Restaurant Analogy:**
> - **Sync:** A waiter takes your order, walks to the kitchen, **stands there waiting for the chef to cook your meal**, and only serves the next customer once your plate is delivered. 5 waiters can only serve 5 tables at a time.
> - **Async:** A waiter takes your order, hands the ticket to the kitchen, immediately attends to 20 other tables, and returns with your plate as soon as the kitchen dings the bell. 1 waiter can easily manage 50 tables.

---

### 2. Why Handlers in `v2` are `async def` compared to `v1`

In **v1** ([`v1/backend/monolith/app/routes/`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/routes)), all handlers were synchronous `def`:

```python
# v1: Synchronous handler in monolith/app/routes/uploads.py
@router.post("/{session_id}/files/register")
def register_files(session_id: str, body: RegisterRequest, background: BackgroundTasks, db: DbSession = Depends(get_db)):
    ...
    background.add_task(ingest.run, session_id)  # Runs inside the same web server thread pool!
```

In **v2** ([`v2/backend/api/routes/`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes)), every API handler is rewritten with `async def`:

```python
# v2: Asynchronous handler in backend/api/routes/uploads.py
@router.post("/{session_id}/files/register")
async def register_files(session_id: str, body: RegisterRequest, db: AsyncSession = Depends(get_session)):
    ...
    await run_in_threadpool(queues.send_many, settings.ingest_queue_url, messages)
```

The core reasons for this architectural change:

---

#### 1. Architectural Rule: *"The API Never Does Slow Work"*
- **In v1:** The web server was a monolith. When a file upload completed, it ran `background.add_task(ingest.run, session_id)` inside the API process. Ingestion (PDF parsing, OCR, whisper audio extraction, embeddings) took minutes and consumed the web server’s thread pool (40 threads). **A few simultaneous uploads would exhaust all 40 threads and freeze the API for every user.**
- **In v2:** The API is purely an orchestration layer. It validates requests, persists metadata in PostgreSQL, pushes messages to an **SQS queue**, and responds in **milliseconds**. Heavy processing was moved to independent background worker fleets ([`v2/backend/workers/ingest.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py) and [`v2/backend/workers/chat.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/chat.py)).

---

#### 2. Server-Sent Events (SSE) Replaced Polling
- **In v1:** The browser polled `GET /sessions/{session_id}/chat` every 2 seconds to check if an assistant answer was ready ([`v1/chat.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/routes/chat.py#L83-L114)). 300 active users produced 150 useless requests per second.
- **In v2:** Handled via Server-Sent Events at [`v2/backend/api/routes/events.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/events.py#L102-L116):
  ```python
  @router.get("/{session_id}/events")
  async def stream_events(session_id: str, request: Request) -> StreamingResponse:
      return StreamingResponse(_stream(session_id, request), media_type="text/event-stream")
  ```
  - An SSE connection stays open indefinitely while the user is on the page.
  - **In a sync server (v1):** 40 open connections would occupy all 40 threads, rendering the server unable to accept any new HTTP requests.
  - **In an async server (v2):** Thousands of idle event streams sit on the `asyncio` event loop at virtually zero CPU and memory cost, waking up only when Redis publishes an event.

---

#### 3. True Non-Blocking Database and Cache Operations
- **In v1:** Used synchronous SQLAlchemy with SQLite / standard PostgreSQL driver (`psycopg2`), which blocks the thread during every query execution.
- **In v2:** As shown in [`v2/backend/shared/db.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/db.py#L41-L56), the API uses:
  - `asyncpg` via SQLAlchemy's `AsyncSession` (`create_async_engine`).
  - `redis.asyncio` for non-blocking Pub/Sub event streaming.
  - Database queries are awaited (`await db.execute(...)`), freeing the event loop while waiting for PostgreSQL network I/O.

---

#### 4. Handling Synchronous Libraries without Blocking the Event Loop
AWS SDK (`boto3`) is synchronous. In v2, instead of blocking the async loop when signing S3 URLs or posting to SQS queues, it safely delegates those calls to background threads using Starlette's `run_in_threadpool`:

```python
# v2/backend/api/routes/uploads.py:71
url = await run_in_threadpool(storage.presign_put, key, spec.content_type)

# v2/backend/api/routes/uploads.py:115
await run_in_threadpool(queues.send_many, settings.ingest_queue_url, messages)
```

---

#### 5. Two-Tier Design: Async API + Sync Workers
Notice how the project architecture in [`v2/backend/shared/db.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/db.py#L1-L11) uses **two database drivers on purpose**:

1. **API Tier (Async):** Uses `postgresql+asyncpg://` — handles thousands of lightweight, short-lived HTTP requests and idle SSE connections concurrently.
2. **Worker Tier (Sync):** Uses `postgresql+psycopg2://` — dedicated workers poll SQS messages in a synchronous loop ([`v2/backend/workers/base.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/base.py)), because blocking while doing heavy GPU/chunking computation on a single task is exactly their intended job.

Viewed uploads.py:41-81




-----


Viewed storage.py:1-60

In [`uploads.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/uploads.py#L69-L73):

```python
# boto3 is synchronous; signing is pure computation but it is not ours
# to block the loop with.
url = await run_in_threadpool(storage.presign_put, key, spec.content_type)
```

---

### The Short Answer
`run_in_threadpool` (from Starlette / FastAPI) **takes a synchronous, blocking function, offloads its execution to a separate background thread pool, and wraps it in a coroutine so you can `await` it without blocking the `asyncio` event loop.**

---

### Why It Is Needed Here

1. **The Endpoint is `async def`:**
   `create_uploads` runs on FastAPI's single-threaded **`asyncio` event loop**. The fundamental rule of async programming is: **never block the event loop with synchronous or slow operations.**

2. **`boto3` is a Synchronous Library:**
   [`storage.presign_put`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/storage.py#L56-L61) uses AWS SDK (`boto3`), which is entirely synchronous. It computes an HMAC-SHA256 signature and formats the URL.

3. **The Problem with Calling It Directly:**
   If you wrote:
   ```python
   # ❌ BAD in an async def function:
   url = storage.presign_put(key, spec.content_type)
   ```
   The entire server's event loop would freeze while computing the cryptographic signature. During that time, **no other incoming requests, database responses, or Server-Sent Events (SSE) could be processed.**

---

### How `await run_in_threadpool(...)` Works Step-by-Step

```
 FastAPI Event Loop Thread                     Worker Thread Pool
 ─────────────────────────                     ──────────────────
 1. Calls run_in_threadpool(...)
    ├─ Dispatches task ───────────────────────► Runs storage.presign_put()
    │                                           (computing cryptographic URL)
 2. "await" yields control
    ├─ Event loop is FREE to handle other
    │  requests / SSE streams in the meantime.
    │                                           Task finishes, returns URL string
    └◄── Receives notification / result ────────┤
 3. Resumes execution with `url` value
```

1. **Dispatch:** `run_in_threadpool` sends `storage.presign_put(key, spec.content_type)` to execute on a separate OS thread in Starlette's worker thread pool.
2. **Non-Blocking Yield:** The `await` keyword tells Python: *"Yield control back to the event loop now. Continue with other users' requests while the thread does the work."*
3. **Resume:** When the thread finishes generating the presigned URL, the event loop wakes up and assigns the return value to `url`.

---

### Where Else It Is Used in `v2`

You will see this pattern applied everywhere in `v2` where synchronous SDKs (like AWS `boto3`) are called from `async def` routes:

- **Sending SQS Messages:** In [`uploads.py:115`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/uploads.py#L115):
  ```python
  await run_in_threadpool(queues.send_many, settings.ingest_queue_url, messages)
  ```
- **Checking SQS Queue Depth:** In [`health.py:62`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/health.py#L62):
  ```python
  await run_in_threadpool(queues.depth, url)
  ```

Viewed uploads.py:46-85



----
### Definition: Single-Threaded `asyncio` Event Loop

A **single-threaded `asyncio` event loop** is a programming mechanism where **one single operating system (OS) thread** manages and executes multiple concurrent tasks by switching between them whenever a task is waiting for an I/O operation (like network, disk, or database).

---

### 1. How It Works (The Core Mechanism)

At its heart, the event loop is an infinite loop that constantly asks:  
*"What task is ready to run right now?"*

```
                 ┌────────────────────────────────┐
                 │       Event Loop (Thread)      │
                 └───────────────┬────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │ Is any task ready now?  │
                    └──────┬────────────▲─────┘
                           │            │
                  Yes      │            │  When I/O completes
          ┌────────────────▼───┐        │  (OS wakes task up)
          │ Run Coroutine code │        │
          │ until next `await` │        │
          └────────┬───────────┘        │
                   │ (Hits I/O wait)    │
                   ▼                    │
          ┌────────────────────┐        │
          │ Register I/O with  │────────┘
          │ OS (epoll / kqueue)│
          └────────────────────┘
```

#### Step-by-Step Execution:
1. **Task Execution:** The thread picks a task and runs its Python code line-by-line.
2. **The Yield Point (`await`):** When the task hits an `await` (e.g., `await db.get(...)` or `await request.is_disconnected()`), it **pauses** itself and hands control back to the event loop.
3. **Registration:** The event loop tells the OS kernel (via `epoll` on Linux or `kqueue` on macOS): *"Notify me when data arrives on this network socket / database connection."*
4. **Switching:** Without waiting, the event loop immediately picks up another task and runs it.
5. **Resume:** As soon as the OS signals that the I/O data is ready, the event loop resumes the paused task from where it left off.

---

### 2. Cooperative vs. Preemptive Multitasking

| Feature | Multi-Threaded (Preemptive) | `asyncio` Event Loop (Cooperative) |
| :--- | :--- | :--- |
| **Number of Threads** | Many (e.g., 40–100 OS threads) | **1 OS thread** |
| **Who switches tasks?** | The **Operating System** abruptly pauses threads at any time. | The **code itself** voluntarily yields control at `await` keywords. |
| **Memory Overhead** | ~1 MB to 8 MB per thread (stack space). | A few hundred bytes per coroutine. |
| **Concurrency Scale** | Hundreds of concurrent tasks before RAM/CPU thrashing. | **Tens of thousands** of concurrent connections easily. |
| **Race Conditions** | High risk; requires mutexes, locks, and semaphores. | Low risk for simple shared state because only one piece of code runs at any exact millisecond. |

---

### 3. The Golden Rule of the Single Thread

Because there is **only one thread**:

> **If any code blocks the thread, the ENTIRE application freezes.**

- **Non-blocking (Correct):**
  ```python
  await asyncio.sleep(5)  # Yields control back to the loop. 
                          # Other requests continue running!
  ```
- **Blocking (Freezes the Server):**
  ```python
  time.sleep(5)           # Freezes the single thread. 
                          # ALL users are locked out for 5 seconds!
  ```

This is precisely why [`run_in_threadpool`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/uploads.py#L71) exists: whenever you must run a synchronous or blocking function (like AWS SDK / `boto3`), you send it to a background thread so the **single-threaded event loop** stays free to serve everyone else.


----
**Yes, your understanding is 100% correct.**

Here is the exact summary:

1. **1 to 40 requests:** Each synchronous request (or background ingestion job) grabs 1 thread from the pool of 40 and blocks it. The remaining threads handle other users simultaneously.
2. **The 41st+ request:** With all 40 threads occupied, any new incoming request is placed in a waiting queue. 
3. **The halt:** If long tasks (like PDF parsing or audio transcription) hog those 40 threads for minutes, the queue fills up, requests time out (504 Gateway Timeout), and the entire server becomes completely unresponsive to everyone.



----
### Why 1 Async Thread is Dramatically Better for 1,000 Requests

It sounds counter-intuitive at first: *How can 1 thread be faster and handle more traffic than 40 threads?*

The secret lies in **what a web server actually spends its time doing: waiting.**

---

### 1. What a Web Server Spends 99% of Its Time Doing

In a web API, a typical request takes ~50 milliseconds, but:
- **Actual CPU computation (Python code):** ~**0.2 milliseconds** (validating input, building JSON).
- **Idle waiting for I/O (Database network, SQS, S3):** ~**49.8 milliseconds**.

```
Sync (v1):   [0.2ms CPU] [─────────────────── 49.8ms BLOCKED WAITING ───────────────────]  <-- Thread is completely frozen!
Async (v2):  [0.2ms CPU] ──► (Yields thread to next request while DB query travels in OS)
```

---

### 2. What Happens When 1,000 Requests Arrive?

#### In `v1` (40 Threads):
1. The first **40 requests** grab the 40 threads.
2. The remaining **960 requests sit blocked in a queue**, waiting for a thread to become free.
3. If an ingestion job or slow DB query is running, threads stay blocked for seconds.
4. Requests time out (504 Error), connections drop, and the server crashes.

#### In `v2` (1 Async Event Loop Thread):
1. **Request #1** arrives $\rightarrow$ The single thread executes Python validation in **0.02ms** $\rightarrow$ hits `await db.get(...)` $\rightarrow$ hands the network socket to the OS kernel.
2. **Without waiting for the DB**, the thread **instantly** grabs **Request #2**, validates it in **0.02ms**, hits `await db.get(...)`.
3. It repeats this for **Request #3, #4 ... #1,000**.
4. Within just **~20 milliseconds**, the single thread has accepted and dispatched database queries for **all 1,000 requests simultaneously**.
5. As PostgreSQL sends results back over the network sockets, the OS wakes the event loop up, and the thread formats and returns responses to all 1,000 users.

> **Result:** All 1,000 users are served in **~60 milliseconds**, using **almost zero extra RAM** and **1 CPU core**.

---

### 3. Why This Works in `v2`: Three Key Pillars

1. **"The API Never Does Slow Work":**
   In v2, the API never parses PDFs or calls LLMs. It only writes a DB row, puts a message in SQS, and returns in milliseconds. The slow work is done by separate background worker processes.
2. **`run_in_threadpool` for Synchronous SDKs:**
   Whenever `boto3` needs to run (like S3 signing or SQS sending), it is offloaded to a background thread so the main async thread never stops.
3. **Multi-Process in Production:**
   While each event loop is 1 thread, in production you run Uvicorn with multiple worker processes (e.g. 4 processes on a 4-core CPU behind an AWS Load Balancer). That means **4 event loops** handling tens of thousands of concurrent requests effortlessly.


---
To answer this, there are two key parts: **the architectural separation in v2**, and **the millisecond-by-millisecond timeline of how the single thread switches between steps.**

---

### Part 1: Ingestion is NOT in the API Process at All

In **v2**, the heavy ingestion work (reading files, extracting text, generating embeddings) does **not run on the API thread at all**.

```
  [Browser 1]                     [Browser 2]
       │                               │
       ▼                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 API PROCESS (Single Thread)                 │
│  • Request 1: Puts a tiny message on SQS (takes 2ms)         │
│  • Request 2: Creates presigned URLs (takes 2ms)            │
└──────────────────────────────┬──────────────────────────────┘
                               │ (Message on SQS Queue)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│              INGEST WORKER PROCESS (Separate CPU)           │
│  • Reads file from S3                                       │
│  • Parses PDF / Transcribes audio / Chunks text             │
│  • Calls GPU for embeddings                                 │
└─────────────────────────────────────────────────────────────┘
```

The API process only spends **~2 milliseconds** creating an SQS message. The actual minutes-long ingestion happens in a completely separate worker process running on another CPU core or server.

---

### Part 2: How the Single Thread Interleaves Steps

Even within the API, Request 1 (`register_files`) and Request 2 (`create_uploads`) have multiple steps. 

Here is the exact **millisecond-by-millisecond timeline** of how 1 thread handles both requests without getting stuck:

```
Time      API Single Thread Action                                           Status of Requests
───────────────────────────────────────────────────────────────────────────────────────────────────
0.00 ms   👉 Takes Request 1 (register_files)                                Request 1: RUNNING
          • Validates file IDs (0.01ms CPU)                                  Request 2: Waiting
          • Hits `await db.execute(...)` (Querying PostgreSQL)               
          • ⏸️ Request 1 yields control!                                     Request 1: PAUSED (Waiting for DB)
                                                                             
0.02 ms   👉 Takes Request 2 (create_uploads)                                Request 2: RUNNING
          • Validates file sizes and extensions (0.01ms CPU)                 
          • Hits `await db.get(...)` (Querying PostgreSQL)                   
          • ⏸️ Request 2 yields control!                                     Request 2: PAUSED (Waiting for DB)

────────────────────────────────── BOTH REQUESTS WAITING ON DB NETWORK ────────────────────────────

5.00 ms   🔔 PostgreSQL finishes DB query for Request 1                     
          👉 Resumes Request 1                                               Request 1: RUNNING
          • Updates session status to PROCESSING (0.01ms CPU)                
          • Hits `await db.commit()`                                         
          • ⏸️ Request 1 yields control!                                     Request 1: PAUSED (Waiting for DB)

5.02 ms   🔔 PostgreSQL finishes DB query for Request 2                     
          👉 Resumes Request 2                                               Request 2: RUNNING
          • Generates file IDs & rows                                        
          • Hits `await run_in_threadpool(storage.presign_put)`              
          • ⏸️ Request 2 yields control (offloaded to thread)                Request 2: PAUSED (Waiting for S3 sign)

6.00 ms   🔔 Request 1 DB commit finishes                                   
          👉 Resumes Request 1                                               Request 1: RUNNING
          • Hits `await run_in_threadpool(queues.send_many, ...)`            
          • ⏸️ Request 1 yields control (offloaded to thread)                Request 1: PAUSED (Waiting for SQS)

6.50 ms   🔔 Request 2 S3 presign finishes                                  
          👉 Resumes Request 2                                               Request 2: RUNNING
          • Commits to DB: `await db.commit()`                               Request 2: PAUSED (Waiting for DB)

7.50 ms   🔔 SQS send for Request 1 finishes                                
          👉 Resumes Request 1                                               
          • ✅ Returns HTTP 200 (RegisterResponse) to User 1                Request 1: COMPLETE!

8.00 ms   🔔 DB commit for Request 2 finishes                               
          👉 Resumes Request 2                                               
          • ✅ Returns HTTP 200 (UploadResponse) to User 2                  Request 2: COMPLETE!
```

---

### The Key Takeaway

The single thread operates like a **chess grandmaster playing 50 simultaneous games**:
1. The grandmaster spends 1 second making a move at Board 1.
2. While Player 1 is thinking (I/O wait), the grandmaster walks to Board 2 and makes a move.
3. While Player 2 is thinking, the grandmaster moves to Board 3.
4. The grandmaster never stands idle waiting for an opponent to think.



----
### 1. What Does Uvicorn Do?

**Uvicorn** is a high-performance **ASGI web server** for Python. 

- It listens on a TCP port (e.g., port `8000`), accepts incoming HTTP/HTTPS connections from clients or load balancers, and parses the raw network bytes into request headers and body.
- It hands those requests to **FastAPI**, drives the **`asyncio` event loop** (using a fast C-based loop called `uvloop`), and sends the generated responses back across the internet.

---

### 2. Does 1 Uvicorn Worker Mean 1 Thread?

**No. 1 Uvicorn Worker = 1 full OS Process (not just a thread).**

Because Python has the **GIL (Global Interpreter Lock)**, multiple threads inside one process cannot run Python code on multiple CPU cores simultaneously. 

To use all CPU cores on an AWS machine, Uvicorn uses **multiprocessing**:

```
                    AWS Machine (e.g. 4-Core CPU)
                    ─────────────────────────────
                       Uvicorn Master Process
                       ┌─────────┬─────────┐
          ┌────────────┼─────────┼─────────┼────────────┐
          ▼            ▼         ▼         ▼            ▼
     Worker 1     Worker 2    Worker 3  Worker 4    (4 OS Processes)
    [Core #1]    [Core #2]   [Core #3] [Core #4]
        │            │           │         │
    1 Event      1 Event     1 Event   1 Event      (Each has 1 async thread)
      Loop         Loop        Loop      Loop
```

- If you specify `--workers 4`, Uvicorn spawns **4 independent operating system processes** (each on its own CPU core with its own separate memory).
- **Inside each worker process**, there is **1 main `asyncio` event loop thread** handling thousands of concurrent async requests (plus a small helper thread pool for `run_in_threadpool`).

---

### Summary Table

| Term | What It Is | Role in Deployment |
| :--- | :--- | :--- |
| **Uvicorn** | ASGI Web Server | Translates raw HTTP network traffic $\leftrightarrow$ FastAPI Python app. |
| **Worker** | **1 OS Process** | Uses 1 full CPU core. 4 workers = 4 CPU cores fully utilized. |
| **Inside 1 Worker** | **1 Async Event Loop** | Uses non-blocking `await` to manage thousands of simultaneous user connections per process. |


----
### How `run_in_threadpool` Works

`run_in_threadpool` uses **Multithreading** (specifically, a **Thread Pool** of up to 40 worker threads inside that process). 

It does **not** use multiprocessing, and it is **not** restricted to just 1 single thread.

---

### The Architecture Inside One Uvicorn Process

```
                      ONE UVICORN WORKER PROCESS
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  [ MAIN THREAD ] ─────────────────────────► Runs asyncio Event Loop      │
│                                              (manages requests, awaits)  │
│                                                                          │
│  [ THREAD POOL ] (e.g. 40 background threads)                           │
│     ├── Worker Thread 1  ──► Executes storage.presign_put(...)           │
│     ├── Worker Thread 2  ──► Executes queues.send_many(...)              │
│     ├── Worker Thread 3  ──► Executes another sync task...               │
│     └── ... up to 40 threads running simultaneously                      │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### What Happens When Multiple Requests Use It?

1. **Borrowing a Thread:**  
   When Request 1 calls:
   ```python
   url = await run_in_threadpool(storage.presign_put, key, spec.content_type)
   ```
   Starlette borrows **Thread #1** from the pool to run `presign_put`.

2. **Multithreaded Parallelism:**  
   If 5 requests hit this endpoint at the exact same moment, `run_in_threadpool` uses **5 separate worker threads** from the pool to compute all 5 presigned URLs simultaneously in parallel.

3. **Reverting to the Main Async Thread:**  
   - While the thread pool is working, the **Main Thread** is completely free to handle other traffic.
   - The moment a worker thread finishes, it signals the event loop: *"Task finished, here is the result."*
   - The main async thread picks up the returned value, resumes the coroutine, and sends the HTTP response back to the client.

---

### Summary

- **Uses:** **Multithreading** (Thread Pool).
- **Does not use:** Multiprocessing (processes are only created by Uvicorn at startup).
- **Benefit:** Allows multiple synchronous tasks to execute across multiple worker threads simultaneously while keeping the main async event loop completely unblocked.

Viewed TUTORIAL.md:1-25



----
Here is a complete breakdown of **Multithreading vs. Multiprocessing**, what the **GIL** is, why Python behaves the way it does, and the workarounds used in modern systems.

---

### 1. Multithreading vs. Multiprocessing

| Feature | Multithreading | Multiprocessing |
| :--- | :--- | :--- |
| **Basic Unit** | Multiple **threads** inside the *same* process. | Multiple completely independent **processes**. |
| **Memory Space** | **Shared memory.** All threads share variables, heap, and open files. | **Isolated memory.** Each process has its own private RAM and PID. |
| **Creation Cost** | Very lightweight; fast to create and switch between. | Heavyweight; takes more memory and CPU time to spawn. |
| **Communication** | Instant (threads read/write the same variables). | Requires IPC (Inter-Process Communication, e.g. Queues, Pipes, Sockets) and data serialization (`pickle`). |
| **Failure Impact** | If one thread segfaults/crashes, the **entire application dies**. | If one worker process crashes, other processes **keep running**. |
| **Best Used For** | **I/O-bound tasks** (network calls, disk reads, waiting on APIs). | **CPU-bound tasks** (math, image processing, machine learning, data parsing). |

---

### 2. What is the GIL (Global Interpreter Lock)?

In standard Python (CPython), the **GIL is a master mutex (lock)** that allows **only one thread to execute Python bytecode at any given millisecond**, regardless of how many CPU cores your machine has.

```
                    4-Core CPU Machine
                    ──────────────────
   Thread 1 ──┐
   Thread 2 ──┼──► [  THE GIL (Lock)  ] ──► ONLY 1 Thread runs Python code at a time!
   Thread 3 ──┘
```

#### Why was the GIL added in the first place?
1. **Memory Management (Reference Counting):** Python tracks memory by counting how many references exist for every object. Without a global lock, two threads modifying reference counts simultaneously could cause memory corruption or memory leaks.
2. **Speed for Single-Threaded Code:** Having one global lock is much faster than having millions of tiny fine-grained locks on every single variable and object.
3. **Easy C-Extensions:** It made it trivial to integrate existing C libraries into Python without writing complex thread-safety code.

---

### 3. Why Can't Python Do Multi-Core Parallelism with Threads?

- If you create **4 threads** to calculate heavy math (CPU-bound) on a 4-core machine:
  - All 4 threads will **fight and battle for the GIL**.
  - The OS constantly pauses one thread and gives the GIL to another (thread thrashing and context switching).
  - **Result:** The 4-threaded code often runs **slower** than a single-threaded loop!

#### The Critical Exception: I/O-Bound Work
Python **releases the GIL** whenever it makes an OS system call or waits for I/O (e.g., `socket.recv()`, reading from disk, or waiting for a database response). 

Therefore, multithreading **does work** for I/O waiting, which is why [`run_in_threadpool`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/uploads.py#L71) can run multiple I/O or C-level tasks across a thread pool.

---

### 4. How Python Finds Workarounds

Since Python threads cannot achieve parallel CPU execution, how do production systems scale?

#### Workaround 1: Multiprocessing (The Standard Backend Approach)
Instead of 1 process with 4 threads, spawn **4 independent processes**.
- Each process has its own Python interpreter and its own separate GIL.
- **Example in your project:** Uvicorn with `--workers 4` or Gunicorn. Each worker process sits on its own CPU core and executes in true parallel.

```
 Core #1 ──► [ Process 1 (with its own GIL) ]
 Core #2 ──► [ Process 2 (with its own GIL) ]
 Core #3 ──► [ Process 3 (with its own GIL) ]
 Core #4 ──► [ Process 4 (with its own GIL) ]
```

#### Workaround 2: Offload to C / C++ / Rust (NumPy, PyTorch, Polars)
Libraries like **NumPy**, **PyTorch**, and **OpenCV** write their heavy loops in C/C++/CUDA. 
- Before starting heavy matrix math or tensor operations, they call `Py_BEGIN_ALLOW_THREADS` to **release the GIL**.
- The C code runs in true multi-core parallel across all CPU cores, then reacquires the GIL only when handing results back to Python.

#### Workaround 3: Asynchronous Event Loops (`asyncio` / FastAPI)
Instead of spawning OS threads that block, use a **single-threaded cooperative event loop** (`async`/`await`).
- 1 thread manages 10,000+ concurrent network connections at virtually zero RAM cost by switching tasks whenever a request is waiting for I/O.

#### Workaround 4: The Future of Python (No-GIL / Free-Threaded Python)
Starting in **Python 3.13+ (PEP 703)**, Python introduced an experimental **free-threaded build** that completely removes the GIL, replacing global reference counts with mimalloc-based thread-safe memory management to allow true multi-threaded CPU parallelism in pure Python.

---

### Summary Checklist

| Problem | Solution |
| :--- | :--- |
| Need to run heavy CPU math/parsing in parallel? | Use **Multiprocessing** (`multiprocessing` / multiple Uvicorn workers) or **C-extensions** (NumPy / PyTorch). |
| Need to handle 10,000 concurrent web requests / SSE streams? | Use **Asyncio Event Loop** (FastAPI / `v2` design). |
| Need to run a blocking synchronous function from an async route? | Use **`run_in_threadpool`** to run it on a background thread. |

Viewed db.py:18-58



----
**This is mostly WRONG**, but it has a grain of truth regarding physical *parallelism*.

Here is the exact breakdown of what is true and what is a misconception:

---

### 1. Can 1 CPU core have more than 1 thread?
**YES, absolutely.** 

A single CPU core can (and routinely does) run **hundreds or thousands of threads**. 
- Right now, your laptop probably has **~4,000 threads** running across just **8 CPU cores**.
- How? The Operating System uses **Time Slicing (Context Switching)**. The OS scheduler gives Thread A 5 milliseconds of CPU time, then pauses it and gives Thread B 5 milliseconds, then Thread C, and so on. Because it switches millions of times per second, all threads *appear* to run at the same time.

---

### 2. The Difference Between *Concurrency* and *Parallelism*

| Concept | What it Means | Cores Required |
| :--- | :--- | :--- |
| **Concurrency** (Multitasking) | Many threads make progress by taking turns sharing CPU cores. | **1 Core is enough** for hundreds of threads. |
| **True Parallelism** | Multiple threads execute CPU instructions at the **exact same physical nanosecond**. | Needs **multiple physical cores** (e.g., 4 threads running CPU math at the exact same moment need 4 cores). |

---

### 3. What Happens in Hardware (Hyper-Threading)

Modern CPU cores (Intel/AMD/Apple) also have hardware **Simultaneous Multi-Threading (Hyper-Threading)**:
- 1 physical CPU core has **2 hardware threads (logical cores)**.
- A 4-core physical CPU typically exposes **8 logical cores** to the operating system.

---

### The Kitchen Analogy

- **1 CPU Core = 1 Chef**
- **Threads = Cooking Tasks** (chopping onions, stirring soup, boiling pasta)

1. **Can 1 chef do 3 tasks?**  
   **Yes!** The chef chops onions for 10 seconds, stirs the soup for 5 seconds, and checks the pasta. The chef is doing 3 tasks **concurrently**.
2. **Can 1 chef chop onions and flip pancakes at the exact same physical second?**  
   **No.** For two actions to happen at the exact same physical millisecond (**true parallelism**), you need **2 separate chefs (2 CPU cores)**.

---

### Summary

- **"1 CPU core cannot have more than 1 thread"** $\rightarrow$ ❌ **Wrong.** 1 core can juggle hundreds of threads concurrently.
- **"4 parallel threads need 4 CPU cores"** $\rightarrow$ ✅ **True for pure CPU math happening at the exact same nanosecond**, but ❌ **False for general computing/I/O**, where 4 threads can easily share 1 core.



----
**Spot on! You have hit the exact hardware reality.**

Here is the exact comparison between how a **CPU** and a **GPU** handle this:

---

### 1. CPU: Latency-Oriented (Few Powerful Cores + Latency Hiding)
- A CPU has **4 to 16 large, complex cores**.
- At any exact nanosecond $t$, **1 CPU core’s execution unit (ALU) is executing instructions for only 1 thread**.
- When you have 100 threads on a CPU core, the CPU is doing **latency hiding & context switching**:
  - While Thread 1 is waiting on a RAM cache-miss or disk/network I/O, the hardware/OS rapidly swaps in Thread 2.
  - It creates the *illusion* of parallelism through fast time-sharing.

---

### 2. GPU: Throughput-Oriented (SIMT — Massively Parallel Hardware)
- A GPU has **thousands of physical ALU cores** (e.g., an Nvidia H100 has over **16,000 CUDA cores**).
- It uses **SIMT (Single Instruction, Multiple Threads)**:
  - Instead of switching back and forth, the GPU groups threads into **Warps** (groups of 32 threads).
  - All 32 threads in a warp execute the **exact same math instruction on 32 distinct pieces of data on 32 separate physical hardware units at the exact same clock cycle**.
- **No context switching needed for execution:** Thousands of threads are literally crunching numbers simultaneously in true physical hardware parallelism.

---

### Summary Comparison

| Metric | CPU (Latency Engine) | GPU (Throughput Engine) |
| :--- | :--- | :--- |
| **Physical Units** | A few complex, ultra-fast cores (4–16). | Thousands of simple, parallel cores (thousands). |
| **Execution at Instant $t$** | **1 thread per physical core/pipeline**. | **Thousands of threads in true lockstep parallel**. |
| **Role of Multithreading** | **Latency hiding** (switching when a thread is idle). | **Massive data parallelism** (e.g. matrix multiplication for LLM embeddings). |

Viewed clients.py:74-113



----
Searched for "8. Server-sent events"
Searched for "## 8. Server-sent events"
Viewed TUTORIAL.md:440-520
Viewed events.py:1-87
Searched for "EventSource"
Viewed useSessionEvents.js:1-60

Here is a complete explanation of **Server-Sent Events (SSE)** in **v2**, why it replaced the polling approach from **v1**, and how it works across a distributed architecture.

---

### 1. The Problem with v1 Polling

In **v1**, the frontend had no way of knowing when background tasks (ingestion or chat answer generation) were finished:

```
[Browser] ──── "Is answer ready?" ────► [API Server] ────► "No, still working..."
[Browser] ──── (waits 2 seconds)
[Browser] ──── "Is answer ready?" ────► [API Server] ────► "No, still working..."
[Browser] ──── (waits 2 seconds)
[Browser] ──── "Is answer ready?" ────► [API Server] ────► "Yes! Here it is."
```

#### Why this failed at scale:
1. **Wasted Network & CPU:** With 300 active users, the frontend sent **150 empty HTTP requests every second**.
2. **Artificial Lag:** If an answer was generated in 100ms, the user could still wait **up to 2 full seconds** for the next poll cycle to detect it.
3. **No Token Streaming:** You couldn't stream LLM tokens word-by-word; the user saw a frozen spinner until the entire answer was done.

---

### 2. What is Server-Sent Events (SSE)?

**Server-Sent Events (SSE)** is a standard web protocol where the browser opens **one long-lived HTTP connection** (`Content-Type: text/event-stream`), and the server continuously pushes updates down to the client whenever there is new information.

- **Standard HTTP:** Uses standard port 80/443 without needing protocol upgrades.
- **Built-in Reconnection:** The browser’s native `EventSource` JavaScript API automatically reconnects if the connection drops.
- **Unidirectional:** Perfect for server-to-client updates (progress bars, status changes, streaming LLM tokens).

---

### 3. How SSE Works in v2 (The Distributed Flow)

In **v2**, the worker doing the job and the API server holding the browser's HTTP connection are **different processes on different machines**. 

To bridge this gap, v2 uses **Redis Pub/Sub**:

```
                                      ┌────────────────────────┐
                                      │ Ingest / Chat Workers  │
                                      │ (Processing task...)   │
                                      └───────────┬────────────┘
                                                  │
                                                  │ 1. Redis Publish:
                                                  │    "events:session_123"
                                                  ▼
                                      ┌────────────────────────┐
                                      │         Redis          │
                                      │     (Message Bus)      │
                                      └───────────┬────────────┘
                                                  │
                                                  │ 2. Subscribed
                                                  ▼
┌──────────────────┐  3. SSE Frame   ┌────────────────────────┐
│  Browser Client  │ ◄────────────── │       API Server       │
│ (useSessionEvents)│  (HTTP stream)  │ (events.py / _stream)  │
└──────────────────┘                 └────────────────────────┘
```

#### Step-by-Step Walkthrough:

1. **Browser Connects via SSE:**
   When a user opens a session, [`useSessionEvents.js`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/frontend/src/useSessionEvents.js#L33) opens a single event stream:
   ```javascript
   const source = new EventSource(`${BASE}/sessions/${sessionId}/events`)
   ```

2. **API Subscribes to Redis Channel:**
   In [`v2/backend/api/routes/events.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/events.py#L54-L86), the async endpoint subscribes to `events:{session_id}` on Redis and keeps an open generator stream:
   ```python
   pubsub = client.pubsub()
   await pubsub.subscribe(events.channel(session_id))
   ```

3. **Workers Publish Events as Work Happens:**
   Whenever a background worker updates file status or generates a word, it calls [`events.publish()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/events.py#L68-L77):
   ```python
   events.publish(session_id, {"event": "token", "delta": "Hello"})
   events.publish(session_id, {"event": "file", "file_id": "...", "status": "ready"})
   ```

4. **API Relays Message to Browser:**
   The API receives the Redis message and yields an SSE frame:
   ```python
   yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
   ```

5. **Browser Reacts in Real Time:**
   The frontend listens for specific event types and updates the UI instantly:
   ```javascript
   source.addEventListener('token', (e) => appendToken(e.data))
   source.addEventListener('file', (e) => updateFileProgress(e.data))
   source.addEventListener('session', (e) => unfreezeChatBox(e.data))
   ```

---

### 4. Why SSE Instead of WebSockets?

| Feature | Server-Sent Events (SSE) | WebSockets |
| :--- | :--- | :--- |
| **Direction** | **One-directional** (Server $\rightarrow$ Client). | Full duplex / bidirectional. |
| **Protocol** | Standard **HTTP / HTTP/2**. | `ws://` (Requires TCP connection upgrade). |
| **Reconnection** | **Automatic** by browser (`EventSource`). | Must be manually coded in JS with custom retry/backoff. |
| **Firewalls / Load Balancers** | Works out-of-the-box through standard proxies/Cloudflare. | Often requires special proxy configuration to maintain persistent upgrades. |
| **Use Case Fit** | Perfect for status updates, progress bars, and LLM token streaming. | Better for multiplayer games, collaborative whiteboards, or chat typing indicators. |

---

### 5. Critical Production Details in the v2 Code

- **The Heartbeat (`yield ": keepalive\n\n"` in [`events.py:78`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/events.py#L78)):**  
  Proxies and load balancers (like AWS ALB or Cloudflare) close connections that stay silent for more than 30–60 seconds. A blank comment frame sent every 15 seconds keeps the connection alive without triggering browser JS events.
- **`X-Accel-Buffering: no` in [`events.py:113`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/events.py#L113):**  
  Nginx buffers HTTP responses by default before sending them to the client. This header tells Nginx: *"Do not buffer; send every byte to the browser immediately."*


-----
Searched for "events.publish"
Viewed chat.py:170-205
Viewed chat.py:120-170
Viewed chat.py:60-110

**Not quite — status is updated in BOTH Redis AND the Database.**

Here is the difference between their roles and why the database is still essential.

---

### 1. Redis Pub/Sub is "Fire and Forget" (Live Notifications)

Redis Pub/Sub does **not store messages permanently**. 
- It acts like a **radio broadcast tower**.
- If the browser is currently tuned in (connected to SSE), it hears the broadcast instantly.
- **If the user refreshes the page, switches tabs, or loses internet for 5 seconds, the Redis broadcast is gone forever.**

In [`v2/backend/workers/chat.py`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/chat.py#L78-L86), the code does this explicitly:

```python
# 1. Publish to Redis (Ephemeral / Live Stream for whoever is watching right now)
publish_token(session_id, message_id, content)

# 2. Persist to Database (Permanent Source of Truth)
row.content = content
row.status = models.MESSAGE_DONE
row.sources = sources
db.commit()
```

---

### 2. What Do We Use the Database For?

The **Database (PostgreSQL / SQLite)** remains the **permanent source of truth**:

1. **Initial Page Load & Page Refreshes:**  
   When a user opens the app or reloads their browser, the frontend calls `GET /sessions/{id}/chat` to fetch the entire conversation history from the **database**.
2. **Persistent Document & File Records:**  
   Tracks uploaded files, file sizes, processing statuses (`models.File`), and sessions (`models.Session`).
3. **Document Chunks (`models.Chunk` table):**  
   Stores the actual parsed text chunks from PDFs/Audio files with indexed lookups so the chat worker can retrieve source context for prompts.
4. **Offline Recovery:**  
   If a user submits a question and immediately closes their laptop, the worker finishes the answer and commits it to the **database**. When the user comes back tomorrow, the answer is still there.

---

### 3. Summary: The Division of Labor

| Component | Technology | Lifespan | Primary Role |
| :--- | :--- | :--- | :--- |
| **Real-Time Notification** | **Redis Pub/Sub** | Instant (Milliseconds) | Pushes live token streams and progress updates to active SSE connections. |
| **Short-Term Context** | **Redis Cache** (`chat:{session_id}`) | 24 Hours | Fast in-memory sliding window of the last 4 conversation turns for prompt assembly. |
| **Permanent State** | **Database** (Postgres) | Permanent | Sessions, file metadata, document text chunks, and full conversation history. |



-----
Viewed chat.py:50-62
Listed directory src
Viewed App.jsx:1-60
Viewed App.jsx:60-120

Here is exactly what happens on page reload:

The browser does **not** fall back to aggressive polling. Instead, it uses a simple **two-step catch-up and reconnect** pattern:

---

### Step 1: Database Fetches the Past (One-Time Catch-up)
The moment the page reloads, React makes **one single HTTP `GET` request** to the API:
- `GET /sessions/{session_id}/chat` $\rightarrow$ Reads the database directly to load all past messages.
- `GET /sessions/{session_id}` $\rightarrow$ Reads the database to check current file statuses (e.g. *3 of 5 files indexed*).

This instantly restores the exact state of the screen up to that millisecond.

---

### Step 2: SSE Immediately Reconnects to Redis (Live Stream)
Inside [`useSessionEvents.js`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/frontend/src/useSessionEvents.js#L33), React’s `useEffect` instantly runs:

```javascript
const source = new EventSource(`${BASE}/sessions/${sessionId}/events`)
```

1. The browser opens a **new SSE connection** to the API.
2. The API subscribes to the Redis channel `events:{session_id}` again.
3. Any new progress from the worker happening after the reload is streamed live to the browser.

---

### What if an event was missed during the 1-second reload?
Because the worker writes to the **Database** as well as **Redis**, the initial `GET` request from **Step 1** catches anything that finished while the browser was reloading.

---

### Is Polling Still Present in v2?

Yes, but only as a **slow safety net** ([`App.jsx:26`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/frontend/src/App.jsx#L26)):

- **In v1:** Aggressive poll every **2 seconds** (the *only* way the frontend got updates).
- **In v2:** Relaxed poll every **15 seconds** (`STATUS_POLL_MS = 15000`) solely as a fallback in case strict corporate firewalls or VPNs drop SSE streaming connections. SSE handles 99.9% of real-time updates instantly.




----
Searched for "broker"
Viewed TUTORIAL.md:330-390
Viewed TUTORIAL.md:390-440

### The Core Problem: Why the Broker Exists

The heavy AI work (like transcribing a 30-minute video with Whisper or generating vector embeddings) runs on **Google Colab GPUs**. 

However:
1. **Google Colab cannot have AWS credentials.** If you paste your AWS Access Key into a Colab notebook, anyone with the notebook link could steal it and access your entire AWS cloud account.
2. **Colab has no fixed address.** Colab runs in Google's cloud behind dynamic firewalls. Your backend cannot easily send requests *inward* to Colab.

---

### The Solution: The Broker Pattern (The "Middleman")

Instead of letting Colab touch AWS, we put a **Broker** (a few safe HTTP endpoints on our own API server) in between.

- **Our API Server:** Has AWS credentials (via IAM Role) and can talk to AWS SQS queues and S3.
- **Colab GPU:** Has **NO AWS credentials**. It only holds a single random password string (`BROKER_TOKEN`).

```
                    WE HOLD AWS CREDENTIALS                        NO CREDENTIALS
                   ┌───────────────────────┐                    ┌──────────────────┐
 [ Ingest Worker ] │                       │                    │                  │
        │          │      API Server       │ 1. "Any work?"     │  Google Colab    │
        ▼          │      (The Broker)     │ ◄───────────────── │   (GPU Worker)   │
   [ SQS Queue ] ──┼─►                     │ 2. "Here is a job" │                  │
                   │                       │ ─────────────────► │ 3. Transcribes   │
                   │                       │ 4. "Still going"   │    audio/video   │
                   │                       │ ◄───────────────── │                  │
                   │                       │ 5. "Done!"         │                  │
                   │                       │ ◄───────────────── │                  │
                   └───────────────────────┘                    └──────────────────┘
```

---

### How Colab Listens and Does Work (The Pull Loop)

Colab uses a simple Python loop that makes standard outbound HTTP `POST` requests to our API:

#### 1. "Do you have work for me?" $\rightarrow$ `POST /broker/claim`
- Colab asks our API: *"Got any transcription jobs?"*
- Our API server talks to AWS SQS on Colab's behalf (`ReceiveMessage`).
- If a message exists, the API returns the job to Colab. If no jobs exist, it returns `204 No Content`.

#### 2. "I am still working on it" $\rightarrow$ `POST /broker/heartbeat`
- A 30-minute video might take 5 minutes to transcribe.
- Every 60 seconds, Colab pings `/broker/heartbeat`.
- Our API tells SQS: *"Colab is still alive and working on this job, don't hand it to anyone else."*

#### 3. "I am finished" $\rightarrow$ `POST /broker/complete`
- Colab tells the Broker: *"Done! The transcript is uploaded."*
- Our API deletes the message from SQS (`DeleteMessage`) and notifies the system.

---

### How Does Colab Read the Video Without S3 Access?

When the Broker hands a job to Colab, the message includes **two temporary Presigned URLs**:

```json
{
  "filename": "lecture.mp4",
  "media_url":  "https://...s3.../lecture.mp4?X-Amz-Signature=...",
  "result_url": "https://...s3.../transcript.json?X-Amz-Signature=..."
}
```

- **`media_url`:** A temporary link allowing Colab to download *only that specific video* using a standard `requests.get()`.
- **`result_url`:** A temporary link allowing Colab to upload `transcript.json` using `requests.put()`.

Colab never learns your AWS bucket name, never sees your AWS account, and needs no AWS SDK (`boto3`).

---

### Why This Design is Brilliant

1. **Zero Security Risk:** If someone steals the Colab `BROKER_TOKEN`, all they can do is ask for a transcription job. They **cannot** delete files, view databases, or run up AWS bills.
2. **No Inbound Tunnels Needed:** Colab always reaches *out* to your API; your API never needs an unstable tunnel into Colab.
3. **Crash Protection:** If Colab runs out of GPU memory or disconnects at minute 29 of a 30-minute video, it never calls `/complete`. AWS SQS automatically makes the job visible again so another worker can pick it up without losing data.



----
Listed directory pipeline
Viewed ingest.py:1-100
Viewed ingest.py:101-200
Viewed ingest.py:201-272
Viewed chat.py:1-100
Viewed chat.py:100-140

Here is the complete **v1 forward pass** for the exact same scenario (**1 Video + 2 PDFs + 1 Chat Question**).

Notice how in v1, **everything runs inside the single web server process on a single thread from the 40-thread pool**, calling functions sequentially one after another.

---

### Phase 1: Session Setup & Synchronous DB Creation

1. **`api.createSession()`** (Frontend)  
   Calls `POST /sessions` to create a session.
2. **[`routes/sessions.py:create_session()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/routes/sessions.py)** (Monolith Web Server)  
   Creates a `models.Session` row in SQLite/Postgres using synchronous `db.commit()` and returns the session ID.
3. **`setInterval(pollStatus, 2000)`** (Frontend)  
   *No SSE in v1.* The browser starts an aggressive timer asking `GET /sessions/{id}` **every 2 seconds** to check if anything is done.

---

### Phase 2: Upload Presigning & Browser Direct Upload

4. **`api.createUploads()`** (Frontend)  
   Sends file names to `POST /sessions/{id}/uploads`.
5. **[`routes/uploads.py:create_uploads()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/routes/uploads.py#L26)** (Monolith Web Server)  
   Synchronously creates 3 `models.File` rows in the DB and calls `storage.presign_put()` for each.
6. **`fetch(upload_url, { method: 'PUT', body: file })`** (Frontend)  
   The browser uploads `video.mp4`, `doc1.pdf`, and `doc2.pdf` to local disk / S3.

---

### Phase 3: Registration & In-Process Thread Hijack

7. **`api.registerFiles()`** (Frontend)  
   Notifies the backend `POST /sessions/{id}/files/register` that uploads finished.
8. **[`routes/uploads.py:register_files()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/routes/uploads.py#L94)** (Monolith Web Server)  
   Sets `session.status = SESSION_PROCESSING` and schedules:
   ```python
   background.add_task(ingest.run, session_id)  # ⚠️ Grabs 1 of 40 web server threads!
   ```

---

### Phase 4: Ingestion — The Sequential In-Memory Loop

*Now running on the web server's thread pool. There are NO queues and NO separate workers.*

9. **[`pipeline/ingest.py:run()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/ingest.py#L37)** (Monolith Background Task)  
   Opens a DB session and starts a **sequential `for` loop** over all 3 files (`for file_row in files:`).

#### Step 4A: Processing File 1 (`video.mp4`)
10. **[`pipeline/ingest.py:process_file()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/ingest.py#L103)** (Monolith Background Task)  
    Detects `kind == KIND_VIDEO` and sets `file_row.status = FILE_PROCESSING`.
11. **[`clients.py:transcribe_by_url()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/clients.py)** (Monolith Background Task)  
    Opens a **blocking HTTP connection across the internet** to the Colab GPU.  
    ⚠️ *The web server thread blocks and waits for 5–20 minutes. If the Colab tunnel drops, the entire job dies.*
12. **[`pipeline/chunk.py:to_jsonl()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/chunk.py)** (Monolith Background Task)  
    Takes the returned transcript JSON, formats chunks, and writes `chunks.jsonl` to storage. Sets `file_row.status = FILE_DONE`.

#### Step 4B: Processing File 2 (`doc1.pdf`)
13. **[`pipeline/ingest.py:process_file()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/ingest.py#L103)** (Monolith Background Task)  
    Downloads `doc1.pdf` from storage to a temporary directory.
14. **[`pipeline/convert.py:to_text()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/convert.py)** (Monolith Background Task)  
    Runs Docling OCR **inside the web server process** (consuming heavy server RAM).
15. **[`pipeline/chunk.py:make_chunks()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/chunk.py)** (Monolith Background Task)  
    Splits text into chunks, writes `doc1_chunks.jsonl` to storage, and sets `file_row.status = FILE_DONE`.

#### Step 4C: Processing File 3 (`doc2.pdf`)
16. **[`pipeline/ingest.py:process_file()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/ingest.py#L103)** (Monolith Background Task)  
    Repeats the exact same conversion and chunking for `doc2.pdf` sequentially.

---

#### Step 4D: Indexing All Chunks (GPU Embedding)
17. **[`pipeline/ingest.py:index_all()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/ingest.py#L226)** (Monolith Background Task)  
    Reads all 3 `chunks.jsonl` files from storage **entirely into Python RAM**, batches them into groups of 256 records, and sends them via heavy HTTP POSTs to Colab (`clients.index_chunks()`).
18. **`session.status = SESSION_READY`** (Monolith Background Task)  
    Commits session ready to DB. On the next 2-second poll tick, the browser discovers the session is ready.

---

### Phase 5: Asking a Question

19. **`api.postMessage()`** (Frontend)  
    Sends the user's question to `POST /sessions/{id}/chat`.
20. **[`routes/chat.py:post_message()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/routes/chat.py#L26)** (Monolith Web Server)  
    Creates user message (`done`) and assistant message (`pending`) in the DB, and schedules:
    ```python
    background.add_task(chat_pipeline.answer, session_id, assistant_message.id, body.content)
    ```
21. **[`pipeline/chat.py:answer()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/chat.py#L31)** (Monolith Background Task)  
    Runs on another thread from the 40-thread pool.
22. **[`clients.py:retrieve()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/clients.py)** (Monolith Background Task)  
    Calls Colab embedding service to find matching chunk IDs.
23. **[`pipeline/chat.py:fetch_chunks()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/pipeline/chat.py#L99)** (Monolith Background Task)  
    ⚠️ *Reads the `.jsonl` files from disk/S3 over the network to find the chunk text* (no SQL chunks table in v1).
24. **[`memory.py:memory.history()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/memory.py)** (Monolith Background Task)  
    Reads chat turns from a **local Python `dict` in RAM** (lost if server restarts).
25. **[`clients.py:generate()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v1/backend/monolith/app/clients.py)** (Monolith Background Task)  
    Calls Colab LLM and blocks until the full response string arrives.
26. **`row.status = MESSAGE_DONE; db.commit()`** (Monolith Background Task)  
    Writes the final answer to the DB.  
27. **Frontend Poll Detects Result** (Frontend)  
    On the next 2-second tick of `GET /sessions/{id}/chat`, the frontend receives the completed answer and renders it.

---

### Side-by-Side Architectural Contrast

| Feature | Version 1 (Monolith) | Version 2 (Distributed Event-Driven) |
| :--- | :--- | :--- |
| **API Endpoints** | Synchronous `def` (blocks a thread per request). | Asynchronous `async def` (non-blocking event loop). |
| **Ingestion Location** | Inside the web server via `background.add_task`. | Decoupled background **Worker Fleet** via SQS queues. |
| **File Processing** | **Sequential** (`for` loop: PDF 2 waits for the 30-min video). | **Parallel** (1 SQS message per file; processed across workers simultaneously). |
| **GPU Communication** | Web server opens a 20-min blocking HTTP connection to Colab. | Colab **pulls** work from a safe **Broker** with temporary Presigned S3 URLs. |
| **Live Updates** | Aggressive frontend **polling** every 2 seconds. | **Server-Sent Events (SSE)** + **Redis Pub/Sub** stream. |
| **Chunk Storage** | Raw `.jsonl` files on disk/S3 (slow disk parsing during chat). | Structured, indexed **`models.Chunk` SQL table**. |
| **Conversation Memory**| Python in-memory `dict` (lost on restart; can't scale across servers). | Shared **Redis Cache** (`chat:{session_id}`). |




-----
Viewed broker.py:1-120
Viewed broker.py:121-276

Here is the granular, step-by-step function call trace of **Version 2**, answering every nuance: how the Broker works, how SQS queues pass messages, how the GPU receives and acknowledges chunks, why Phase 4 uses the Broker while Phase 5 does not, and how the dual database engine operates.

---

### Architectural Map of the 4 SQS Queues & Process Tiers

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. API SERVER (Async def + AsyncSession / aiosqlite / asyncpg)                           │
│    • /sessions/uploads  ──► Hands out Presigned S3 URLs                                  │
│    • /sessions/register ──► Pushes 1 message per file to [ INGEST QUEUE ]                │
│    • /sessions/chat     ──► Pushes question to [ CHAT QUEUE ]                            │
│    • /sessions/events   ──► Persistent SSE stream (relays Redis Pub/Sub)                 │
│    • /broker/*          ──► Secure proxy to AWS SQS on behalf of Colab GPU               │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────────────┐
        ▼                       ▼                               ▼
 [ INGEST QUEUE ]        [ CHAT QUEUE ]                  [ STT QUEUE ] & [ EMBED QUEUE ]
        │                       │                               │
        ▼                       ▼                               ▼
 [ Ingest Worker ]       [ Chat Worker ]                 [ Colab GPU via Broker ]
 (Docling, Chunking,     (Direct HTTP to                 (Whisper & Embeddings;
  DB write, S3 .jsonl)    Retrieval / LLM)                never touches AWS directly)
```

---

### Phase 1: Session Setup & Live SSE Connection

1. **`api.createSession()`** (Frontend)  
   User opens the app; browser calls `POST /sessions`.
2. **[`backend/api/routes/sessions.py:create_session()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/sessions.py#L22)** (API Server)  
   - Runs on the `asyncio` event loop.
   - Uses `AsyncSession` (`await db.commit()`) to insert a new row in table `models.Session` with status `"setup"`.
   - Returns `{ "session_id": "sess_123" }`.
3. **[`frontend/src/useSessionEvents.js:useEffect()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/frontend/src/useSessionEvents.js#L33)** (Frontend)  
   Instantiates `const source = new EventSource('/sessions/sess_123/events')`.
4. **[`backend/api/routes/events.py:stream_events()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/events.py#L102)** $\rightarrow$ **`_stream()`** (API Server)  
   - Connects to `redis.asyncio` and executes `await pubsub.subscribe("events:sess_123")`.
   - Yields the initial SSE frame: `event: open\ndata: {"session_id": "sess_123"}\n\n`.
   - The connection remains idle on the `asyncio` event loop at zero CPU cost.

---

### Phase 2: Upload Presigning & Direct-to-S3 Upload

*The user selects `video.mp4` (30 min), `doc1.pdf`, and `doc2.pdf`.*

5. **`api.createUploads()`** (Frontend)  
   Sends file metadata to `POST /sessions/sess_123/uploads`.
6. **[`backend/api/routes/uploads.py:create_uploads()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/uploads.py#L38)** (API Server)  
   - Validates file sizes against `settings.max_upload_bytes`.
   - Creates 3 database rows in `models.File` with status `FILE_PENDING`.
   - Calls `await run_in_threadpool(storage.presign_put, key, content_type)` for each file.
7. **[`backend/shared/storage.py:presign_put()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/storage.py#L56)** (Worker Thread Pool)  
   Uses `boto3` to cryptographically sign 3 direct-to-S3 `PUT` URLs.
8. **`await db.commit()`** (API Server)  
   Asynchronously commits the 3 file records and returns the presigned URLs to the browser.
9. **`fetch(upload_url, { method: 'PUT', body: fileBytes })`** (Frontend)  
   The browser uploads the 3 files **directly to Amazon S3**. Zero file bytes enter the Python backend.

---

### Phase 3: Registration & SQS Queueing

10. **`api.registerFiles()`** (Frontend)  
    Browser alerts the backend `POST /sessions/sess_123/files/register` that uploads completed.
11. **[`backend/api/routes/uploads.py:register_files()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/uploads.py#L81)** (API Server)  
    - Updates `session.status = SESSION_PROCESSING`, `files_total = 3`, `files_done = 0`.
    - Assembles 3 distinct JSON messages:
      ```json
      [
        {"session_id": "sess_123", "file_id": "file_vid", "kind": "video", "raw_key": "..."},
        {"session_id": "sess_123", "file_id": "file_pdf1", "kind": "pdf", "raw_key": "..."},
        {"session_id": "sess_123", "file_id": "file_pdf2", "kind": "pdf", "raw_key": "..."}
      ]
      ```
12. **[`backend/shared/queues.py:send_many()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/queues.py)** (via `run_in_threadpool`)  
    Pushes the 3 messages onto **`ingest_queue_url`**. The API returns HTTP 200 in 2 milliseconds.

---

### Phase 4: Ingestion Workers, The Broker & The GPU

*The 3 files are picked up from SQS and processed in parallel.*

```
                                  PARALLEL INGESTION
          ┌───────────────────────────────┬───────────────────────────────┐
          ▼                               ▼                               ▼
    [ PDF 1 File ]                  [ PDF 2 File ]                  [ Video File ]
  • Download from S3              • Download from S3              • Create Presigned URLs
  • Docling text extract          • Docling text extract          • Push to [ STT QUEUE ]
  • Chunk text                    • Chunk text                    • Colab Whisper transcribes
  • Write to `models.Chunk`       • Write to `models.Chunk`       • Push transcript to S3
  • Write `.jsonl` to S3          • Write `.jsonl` to S3          • Broker re-queues file
  • Push to [ EMBED QUEUE ]       • Push to [ EMBED QUEUE ]       • Ingest worker chunks text
          │                               │                       • Write to `models.Chunk`
          │                               │                       • Push to [ EMBED QUEUE ]
          └───────────────────────────────┼───────────────────────────────┘
                                          ▼
                         [ Colab GPU Embeds All Chunks ]
                                          ▼
                         `POST /broker/complete` (File DONE)
                                          ▼
                            Session becomes READY!
```

---

#### 4A. Processing the 2 PDFs (Processed by Ingest Worker)

13. **[`workers/base.py:Worker.run()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/base.py#L56)** (Ingest Worker)  
    Pulls a PDF message from `ingest_queue_url` and calls `handle(body)`.
14. **[`workers/ingest.py:handle()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py#L59)** (Ingest Worker)  
    - Uses synchronous `with worker_session() as db:` to set `row.status = FILE_PROCESSING`.
    - Calls `bookkeeping.announce_file()` $\rightarrow$ calls `events.publish()` to Redis $\rightarrow$ SSE updates UI with a progress spinner for this PDF.
15. **[`workers/ingest.py:_prepare_document()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py#L151)** (Ingest Worker)  
    - `storage.download_to(raw_key, tmp_path)`: Downloads the PDF from S3 to disk.
    - `convert.to_text_file(tmp_path, kind, text_path)`: Runs Docling OCR to produce `extracted.txt`.
    - `iter_file_chunks(text_path)`: Generates semantic chunks without loading the entire document into RAM.
16. **[`workers/ingest.py:_store_and_queue()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py#L181)** (Ingest Worker)  
    - **Saves text chunks directly to Database:** Executes `insert(models.Chunk).on_conflict_do_update(...)` (idempotent upsert).
    - **Creates `.jsonl` for the GPU:** Writes chunk records to a local `.jsonl` file and uploads it to S3 (`storage.upload_file(chunks_key, ...)`).
    - **Enqueues GPU Embedding Job:** Puts a message onto **`embed_queue_url`**:
      ```json
      {
        "session_id": "sess_123",
        "file_id": "file_pdf1",
        "filename": "doc1.pdf",
        "count": 42,
        "chunks_url": "https://s3.../chunks.jsonl?X-Amz-Signature=..."
      }
      ```

---

#### 4B. Processing the 30-Minute Video (Speech-to-Text via Broker)

17. **[`workers/ingest.py:handle()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py#L59)** (Ingest Worker)  
    Detects `row.kind == KIND_VIDEO` and calls `_request_transcription(row)`.
18. **[`workers/ingest.py:_request_transcription()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py#L94)** (Ingest Worker)  
    Creates two 6-hour Presigned S3 URLs and pushes an STT task to **`stt_queue_url`**:
    ```json
    {
      "session_id": "sess_123",
      "file_id": "file_vid",
      "filename": "video.mp4",
      "media_url": "https://s3.../video.mp4?X-Amz-Signature=...",
      "result_url": "https://s3.../transcript.json?X-Amz-Signature=..."
    }
    ```
19. **`POST /broker/claim`** (Colab GPU STT Worker $\rightarrow$ Broker API)  
    - Colab GPU asks: `{"job": "stt"}`.
    - [`routes/broker.py:claim()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/broker.py#L135) calls `queues.receive(stt_queue_url)` and returns the job + an encrypted `lease` string.
20. **Colab GPU Transcribes Audio:**  
    - Downloads video bytes from `media_url` via `requests.get()`.
    - Every 60 seconds, pings `POST /broker/heartbeat` with `lease` so SQS does not release the message.
    - Runs Whisper to produce `transcript.json`.
    - Uploads `transcript.json` directly to S3 using `result_url` via `requests.put()`.
21. **`POST /broker/complete`** (Colab GPU $\rightarrow$ Broker API)  
    - Colab GPU notifies: `{"lease": "...", "job": "stt", "ok": true}`.
    - [`routes/broker.py:_advance()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/broker.py#L219) receives this and puts the file **back on `ingest_queue_url`** with `"stage": "transcribed"`.
    - Broker deletes the message from `stt_queue_url`.
22. **[`workers/ingest.py:_chunk_transcript()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/ingest.py#L119)** (Ingest Worker)  
    Ingest worker picks up the transcribed message, parses the transcript into chunks, upserts them into `models.Chunk`, uploads `video_chunks.jsonl` to S3, and puts a task onto **`embed_queue_url`**.

---

#### 4C. Indexing All Chunks (Embedding via Broker)

23. **`POST /broker/claim`** (Colab Embedding Service $\rightarrow$ Broker API)  
    - Colab calls `POST /broker/claim` with `{"job": "embed"}`.
    - Broker pulls from `embed_queue_url` and hands over `chunks_url` + `lease`.
24. **Colab Embeds Vectors:**  
    - Downloads `.jsonl` from S3 using `chunks_url`.
    - Computes vector embeddings and saves them to vector storage (ChromaDB / Milvus).
25. **`POST /broker/complete`** (Colab GPU $\rightarrow$ Broker API)  
    - Colab posts `{"lease": "...", "job": "embed", "ok": true, "indexed": 42}`.
    - [`routes/broker.py:_advance()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/broker.py#L232):
      1. Marks `models.File` row status as `FILE_DONE`.
      2. Calls [`bookkeeping.finish_file(db, session_id)`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/bookkeeping.py#L21): Recounts completed files. Since all 3 files (1 video + 2 PDFs) are `FILE_DONE`, it sets `session.status = SESSION_READY`.
      3. Publishes `event: session` to Redis channel `events:sess_123`.
      4. Broker deletes the message from `embed_queue_url`.
26. **SSE Stream $\rightarrow$ Browser** (Frontend)  
    The API's open `_stream()` receives the Redis event and streams it to the browser. React receives `event: session` and unfreezes the chat input box.

---

### Phase 5: Asking a Question (Why No Broker Here?)

*User submits: "What are the key conclusions?"*

> **Why NO Broker or SQS for Retrieval / LLM Generation?**  
> In Phase 4 (Ingestion), tasks take minutes, so queuing is essential.  
> In Phase 5 (Chat), a **live human is sitting at their screen waiting for an answer**. Passing search and generation through SQS and a pull-broker loop would add 2–5 seconds of artificial polling latency. Therefore, the **Chat Worker makes direct, fast HTTP calls** with circuit breakers to Colab.

27. **`api.postMessage()`** (Frontend)  
    Sends the question to `POST /sessions/sess_123/chat`.
28. **[`backend/api/routes/chat.py:post_message()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/api/routes/chat.py#L20)** (API Server)  
    - Creates `models.Message` for User (`done`) and Assistant (`pending`) in the DB.
    - Pushes a message to **`chat_queue_url`** and returns `202 Accepted` immediately.
29. **[`workers/chat.py:handle()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/chat.py#L42)** (Chat Worker)  
    Pulls the task from `chat_queue_url`.
30. **[`shared/clients.py:retrieve()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/clients.py)** (Chat Worker $\rightarrow$ Direct HTTP to GPU)  
    Direct POST to the vector service $\rightarrow$ returns the winning `chunk_key` IDs and similarity scores (no text).
31. **[`workers/chat.py:_fetch_sources()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/chat.py#L110)** (Chat Worker)  
    Queries PostgreSQL table `models.Chunk` (`SELECT * FROM chunks WHERE chunk_key IN (...)`) to instantly get the text.
32. **[`shared/events.py:history()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/events.py#L57)** (Chat Worker)  
    Reads the last 4 turns from the Redis sliding window `chat:sess_123`.
33. **[`workers/chat.py:build_prompt()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/chat.py#L149)** (Chat Worker)  
    Assembles system instructions + retrieved context + conversation window + user question.
34. **[`shared/clients.py:generate()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/shared/clients.py)** (Chat Worker $\rightarrow$ Direct HTTP to GPU)  
    Calls the LLM service to generate the answer.
35. **[`workers/chat.py:publish_token()`](file:///Users/shubhamaditya/EdgentBooks/AjayShenoy_GroupLectureNotes/AdvanceAIEngineering/projects/python_project/v2/backend/workers/chat.py#L179)** (Chat Worker)  
    Publishes the answer to Redis $\rightarrow$ API SSE stream relays it $\rightarrow$ React streams the markdown answer onto the screen.
36. **`row.content = content; row.status = MESSAGE_DONE; db.commit()`** (Chat Worker)  
    Persists the completed answer in PostgreSQL and pushes the new turn to the Redis conversation window.



    
