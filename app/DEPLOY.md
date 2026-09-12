# Deploying EdgentRAG v2

A runbook. Follow it top to bottom the first time; after that only
[step 10](#10-redeploying) matters.

The deployment is version 1's — **one EC2 machine, one Cloudflare tunnel, SQLite
on local disk.** Nothing is managed, nothing is autoscaled, and there is no load
balancer. What changed since version 1 is inside the box: six containers instead
of one process, four SQS queues between them, and a **broker** the GPU pulls its
own work from.

```
your laptop ──rsync──► EC2 ──┬─ web        nginx + the built frontend
                             ├─ api        async; also hosts /broker  ◄── Colab GPU
                             ├─ ingest-worker ─┐                          asks for work
                             ├─ chat-worker  ──┤ poll SQS
                             ├─ redis          │
                             └─ cloudflared ───┘ outbound only, gives you https
```

Four queues: `ingest` and `chat` are drained by the workers above; `stt` and
`embed` are drained by the GPU, which holds no AWS credentials and reaches the
broker instead.

**Contents**

1. [What you need before starting](#1-what-you-need-before-starting)
2. [Open SSH to the instance](#2-open-ssh-to-the-instance)
3. [Give the instance permission to use SQS](#3-give-the-instance-permission-to-use-sqs)
4. [Install Docker on the box](#4-install-docker-on-the-box)
5. [Copy the code up](#5-copy-the-code-up)
6. [Create the queues](#6-create-the-queues)
7. [Write `.env`](#7-write-env)
8. [Start it and get the URL](#8-start-it-and-get-the-url)
9. [Point the GPU at the broker](#9-point-the-gpu-at-the-broker)
10. [Redeploying](#10-redeploying)
11. [Verifying it actually works](#11-verifying-it-actually-works)
12. [Troubleshooting](#12-troubleshooting)
13. [Shutting down](#13-shutting-down)

---

## 1. What you need before starting

| | value | where it comes from |
|---|---|---|
| EC2 instance | `100.24.124.31` | already running |
| SSH key | `~/Downloads/edgentrag.pem` | created with the instance |
| S3 bucket | `edgentrag-shubham-2026` | version 1, reused as-is |
| AWS account | `117227382643` | the project account |
| Region | `us-east-1` | |
| Colab services | three `https://…trycloudflare.com` addresses | started separately |
| Broker token | any random string | generated in [step 6](#6-create-the-queues) |

The instance should be at least **t3.small (2 GB RAM)**. Six containers, one of
which carries Docling's layout models, will not fit comfortably in a
`t2.micro`'s 1 GB. If that is what you have, the box will build the images and
then start killing containers out of memory — see
[troubleshooting](#12-troubleshooting).

Check the key's permissions before anything else, because SSH silently refuses a
world-readable key:

```bash
chmod 400 ~/Downloads/edgentrag.pem
```

---

## 2. Open SSH to the instance

**This is the current blocker.** Port 22 is filtered — the security group still
allows whichever IP it was created with, and your address has changed since.

Find your current public address:

```bash
curl -s https://checkip.amazonaws.com
```

Then, in the AWS console: **EC2 → Instances → your instance → Security →** click
the security group → **Edit inbound rules → Add rule**

| field | value |
|---|---|
| Type | SSH |
| Protocol | TCP |
| Port | 22 |
| Source | *My IP* — the console fills your address in for you |

Or, from a shell authenticated to account `117227382643`:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxx \
  --protocol tcp --port 22 \
  --cidr $(curl -s https://checkip.amazonaws.com)/32
```

Confirm:

```bash
ssh -i ~/Downloads/edgentrag.pem ubuntu@100.24.124.31 'uptime'
```

> **Nothing else needs opening.** The tunnel makes an *outbound* connection to
> Cloudflare and traffic comes back down it, so the app is reachable on https
> without a single inbound rule for it. Port 8080 stays closed, and so does the
> API's 8000 — which is not published to the host at all.

If your IP changes again (most home connections are dynamic), SSH stops working
and you repeat this step. That is the cost of not using Session Manager.

---

## 3. Give the instance permission to use SQS

Version 1's IAM role only granted S3, because S3 was all it used. Version 2 adds
four queues, so the role needs SQS as well. Skip this and every container starts
fine and then fails on its first message with `AccessDenied`.

Console: **IAM → Roles →** the role attached to the instance → **Add
permissions → Create inline policy → JSON**:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Queues",
      "Effect": "Allow",
      "Action": [
        "sqs:CreateQueue",
        "sqs:GetQueueAttributes",
        "sqs:SetQueueAttributes",
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility"
      ],
      "Resource": "arn:aws:sqs:us-east-1:117227382643:edgentrag-*"
    },
    {
      "Sid": "Bucket",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::edgentrag-shubham-2026/*"
    }
  ]
}
```

`CreateQueue` and `SetQueueAttributes` are only needed for
[step 6](#6-create-the-queues); you can remove them afterwards.

> **Why a role and not an access key.** The instance asks its own metadata
> service for temporary credentials that rotate automatically. No key is written
> to disk, committed, or copied into `.env` — which is why there is not a single
> secret anywhere in this repository.

---

## 4. Install Docker on the box

Version 1 ran from a virtualenv and systemd, so Docker is almost certainly not
there yet.

```bash
ssh -i ~/Downloads/edgentrag.pem ubuntu@100.24.124.31
```

then, on the box:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
```

**Log out and back in** — group membership is only read at login, so `docker ps`
fails with a permission error until you do.

```bash
exit
ssh -i ~/Downloads/edgentrag.pem ubuntu@100.24.124.31
docker compose version        # v2.x — note: `compose`, not `docker-compose`
```

While you are there, stop version 1 if it is still running, or it will hold
port 8080 and run a second tunnel:

```bash
sudo systemctl disable --now edgentrag edgentrag-tunnel 2>/dev/null || true
```

### Give the small instances some swap

A 2 GB box can run out of memory while `npm ci` builds the frontend. Two GB of
swap costs nothing and turns a failed build into a slow one:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 5. Copy the code up

From **your laptop**, in the `v2` directory:

```bash
./deploy.sh ubuntu@100.24.124.31 ~/Downloads/edgentrag.pem
```

This rsyncs the project to `~/edgentrag-v2` on the server. It deliberately
excludes four things:

| excluded | why |
|---|---|
| `.env` | the server's copy holds its own queue URLs; overwriting it would be a bad surprise |
| `frontend/node_modules`, `frontend/dist` | the frontend is built *inside* its image; your local build is irrelevant |
| `__pycache__`, `.venv` | architecture-specific, and rebuilt anyway |

`--delete` is on, so a file you removed locally is removed on the server too.
The server directory is a mirror of yours, not an accumulation.

**The git alternative.** If the code is pushed somewhere, skip `deploy.sh`
entirely:

```bash
ssh -i ~/Downloads/edgentrag.pem ubuntu@100.24.124.31
git clone <your-repo> edgentrag-v2
```

Both routes end in the same place. Use whichever you prefer; the remaining steps
are identical.

---

## 6. Create the queues

Run this **on the box**, so it picks up the instance's IAM role and you do not
need AWS credentials on your laptop.

```bash
cd ~/edgentrag-v2
docker build -f docker/api.Dockerfile -t edgentrag/api .
docker run --rm -e AWS_REGION=us-east-1 edgentrag/api python -m scripts.bootstrap
```

The build is not wasted work — [step 8](#8-start-it-and-get-the-url) reuses
every layer of it from cache.

It creates **eight** queues — four for work, four dead-letter — and prints the
URLs plus a freshly generated broker token:

```
edgentrag-ingest       https://sqs.us-east-1.amazonaws.com/117227382643/edgentrag-ingest
edgentrag-ingest-dlq   https://…/edgentrag-ingest-dlq  (after 5 attempts)
edgentrag-chat         https://…/edgentrag-chat
edgentrag-stt          https://…/edgentrag-stt
edgentrag-embed        https://…/edgentrag-embed
…

Put these in .env:

INGEST_QUEUE_URL=…
CHAT_QUEUE_URL=…
STT_QUEUE_URL=…
EMBED_QUEUE_URL=…

# The GPU's token. It grants "ask for a job" and nothing else.
BROKER_TOKEN=4f3c…
```

**Copy all five lines.** The next step needs them.

The four queues split in two. `ingest` and `chat` are drained by our own
workers inside the compose network. `stt` and `embed` are drained by the
**GPU**, which never sees these URLs — it reaches the broker instead. Section
5 of `TUTORIAL.md` explains why.

The script is idempotent — running it twice is harmless and prints the same
URLs, so re-run it any time you have lost them.

> **Why four queues.** Each work queue has a **dead-letter queue** behind it. A
> message that fails five times is moved there instead of being retried for
> ever. Without one, a single unprocessable file occupies a worker permanently;
> with one, it becomes a number you can look at.

---

## 7. Write `.env`

On the box:

```bash
cd ~/edgentrag-v2
cp .env.example .env
nano .env
```

Paste the two queue URLs in. The finished file:

```ini
ENV=aws
LOG_LEVEL=INFO

S3_BUCKET=edgentrag-shubham-2026
AWS_REGION=us-east-1

INGEST_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/117227382643/edgentrag-ingest
CHAT_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/117227382643/edgentrag-chat
STT_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/117227382643/edgentrag-stt
EMBED_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/117227382643/edgentrag-embed

BROKER_TOKEN=4f3c9a…the value bootstrap printed…

WEB_PORT=8080

EMBEDDING_SERVICE_URL=http://localhost:8001
STT_SERVICE_URL=http://localhost:8002
LLM_SERVICE_URL=http://localhost:8003
```

Two things worth understanding about this file:

**There are no AWS credentials in it.** The IAM role supplies those. If you
find yourself pasting an access key here, something has gone wrong in
[step 3](#3-give-the-instance-permission-to-use-sqs).

**`BROKER_TOKEN` is the one secret, and it is deliberately a weak one.** It
lets the GPU ask for a job and nothing else — it cannot read the bucket, list a
queue, or reach anything else in the account. You will paste the same value
into Colab in [step 9](#9-point-the-gpu-at-the-broker).

**The three service URLs are placeholders and stay that way.** The real Colab
addresses are set on the app's first screen and kept in Redis — because a Colab
tunnel is renamed every time the runtime restarts, and a value baked into `.env`
would be stale within the hour.

Compose will refuse to start if `S3_BUCKET`, `INGEST_QUEUE_URL` or
`CHAT_QUEUE_URL` is empty. That is deliberate: a missing queue URL would
otherwise surface much later as work that silently never happens.

---

## 8. Start it and get the URL

```bash
docker compose up -d --build
```

First run takes several minutes — it builds three images, one of which
downloads Docling's layout and OCR models. Later runs are seconds.

```bash
docker compose ps
```

Six services, all `running`:

```
web  api  ingest-worker  chat-worker  redis  cloudflared
```

Then:

```bash
make url
```

which prints something like `https://gentle-forest-1234.trycloudflare.com`.
**That address changes every time the tunnel restarts** — it is a free quick
tunnel with no domain behind it. Re-run `make url` after any restart rather than
bookmarking it.

Check the app can reach everything it depends on:

```bash
make ready
```

```json
{
  "status": "ok",
  "checks": {"database": true, "redis": true, "ingest_queue": true, "chat_queue": true}
}
```

A `false` here names the thing that is wrong, which is the whole reason the
endpoint reports each dependency separately. `"status": "degraded"` with
`ingest_queue: false` is almost always
[step 3](#3-give-the-instance-permission-to-use-sqs) or a typo in a queue URL.

---

## 9. Point the GPU at the broker

Two directions have to be connected, and they are not the same direction.

**Us → them, for the interactive calls.** Open the tunnel URL in a browser. The
first screen asks for the three Colab addresses. Paste them in and submit; the
app checks each one identifies itself correctly, so a pair of swapped URLs is
caught here rather than halfway through an upload. These are used only for
`/retrieve` and `/generate` — the two calls where somebody is waiting.

**Them → us, for the queued work.** On Colab, in `services/.env`:

```ini
BROKER_URL=https://your-tunnel.trycloudflare.com/api
BROKER_TOKEN=4f3c9a…the same value as the backend's .env…
```

Then start the services as usual. The log tells you which mode each is in:

```
pulling stt jobs from https://your-tunnel.trycloudflare.com/api
pulling embed jobs from https://your-tunnel.trycloudflare.com/api
```

If you see `no broker configured; stt answers HTTP only`, one of those two
values is missing and transcription will queue up and never be collected.

Note the asymmetry: the GPU dials **out** to us for its jobs, so nothing has to
reach into Colab for that half. The tunnel is only still needed for the two
interactive calls.

---

## 10. Redeploying

This is the loop you will actually use day to day. From your laptop:

```bash
./deploy.sh ubuntu@100.24.124.31 ~/Downloads/edgentrag.pem
ssh -i ~/Downloads/edgentrag.pem ubuntu@100.24.124.31 \
  'cd edgentrag-v2 && docker compose up -d --build'
```

or, with git:

```bash
ssh -i ~/Downloads/edgentrag.pem ubuntu@100.24.124.31 \
  'cd edgentrag-v2 && git pull && docker compose up -d --build'
```

Three things to know about what that does:

**Rebuilds are incremental.** Only the layers after your change are redone. The
Dockerfiles install dependencies *before* copying source precisely so that a
code edit never reinstalls Docling.

**The database survives.** It lives in the `appdata` volume, not in a container.
`docker compose down` and `up` leave it alone; only `make clean` (which is
`down -v`) destroys it.

**In-flight work is not lost.** A worker receiving `SIGTERM` stops taking new
messages and finishes the one it holds. Anything it had not acknowledged returns
to the queue and is picked up by the new container — which is exactly the
guarantee version 1 did not have, where restarting during an ingestion lost the
work with no record it had existed.

Deploy while a file is processing and watch it complete anyway. It is worth
doing once, deliberately, to see it.

---

## 11. Verifying it actually works

An end-to-end pass, using version 1's sample files:

1. Open the tunnel URL, enter the three Colab addresses.
2. Upload `../v1/sample_docs/lecture.mp4` and a `.txt` file.
3. Watch progress. It arrives over **server-sent events**, not polling — the
   page opens one long-lived connection and the workers push to it through
   Redis.
4. Ask a question once ingestion finishes. The answer streams token by token.

While that runs, from a second terminal on the box:

```bash
make queues
```

```json
{"ingest": {"waiting": 2, "in_flight": 1}, "chat": {"waiting": 0, "in_flight": 0}}
```

**Waiting** is the backlog; **in-flight** is what workers currently hold. If
waiting climbs while in-flight stays flat, you need more consumers:

```bash
make scale-ingest N=3
```

Three processes on one queue. SQS gives each message to exactly one of them, so
they divide the work without any coordination between them — no locks, no
leader, no shared list. That property belongs to the queue, not to your code.

SQLite tolerates the extra writers because they are all on this machine and the
file is in WAL mode. Confirm that if you like:

```bash
docker compose exec api python -c "import sqlite3; \
  print(sqlite3.connect('/data/edgentrag.db').execute('PRAGMA journal_mode').fetchone())"
```

`('wal',)`. In version 1's default mode those writers would have been blocking
the readers serving the page you are watching.

### Useful commands

```bash
make logs                              # everything, following
docker compose logs -f ingest-worker   # one service
make ps                                # what is running
make ready                             # dependency check
make queues                            # backlog
make url                               # the tunnel address
make shell-db                          # list the tables
```

---

## 12. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `ssh: connect … Operation timed out` | security group does not allow your IP | [step 2](#2-open-ssh-to-the-instance) |
| `Permissions 0644 … are too open` | key is world-readable | `chmod 400 ~/Downloads/edgentrag.pem` |
| `permission denied … docker.sock` | group membership not picked up | log out and back in after `usermod` |
| `required variable INGEST_QUEUE_URL is missing` | `.env` absent or incomplete | [step 7](#7-write-env) |
| `make ready` shows `ingest_queue: false` | role lacks SQS, or a bad URL | [step 3](#3-give-the-instance-permission-to-use-sqs) |
| `make ready` shows `redis: false` | redis still starting | wait, then `docker compose ps` |
| upload fails in the browser console with a CORS error | bucket CORS does not allow the current tunnel origin | see below |
| containers restart in a loop, `docker compose ps` shows exit 137 | out of memory | add swap ([step 4](#4-install-docker-on-the-box)), or a bigger instance |
| `make url` prints nothing | tunnel has not connected yet | `docker compose logs cloudflared` |
| tunnel URL returns 502 | nginx is up, API is not | `docker compose logs api` |
| a file stays "processing" for ever | worker is stuck or crashed | `docker compose logs ingest-worker`; check the DLQ |
| ingestion fails immediately | Colab service address is stale | re-enter the three addresses on the first screen |

**The CORS one deserves explaining.** The browser uploads straight to S3 using a
presigned URL, so the bucket has to accept requests from whatever origin the app
is served from — and the tunnel hands you a *new* hostname every restart. Either
allow all origins (fine for a class, not for production):

```bash
aws s3api put-bucket-cors --bucket edgentrag-shubham-2026 --cors-configuration '{
  "CORSRules": [{
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "PUT"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3000
  }]
}'
```

or re-run it with the current tunnel URL each session.

**Checking the dead-letter queues.** If files fail repeatedly, that is where the
messages went:

```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/117227382643/edgentrag-ingest-dlq \
  --attribute-names ApproximateNumberOfMessages
```

Anything above zero means five consecutive failures on the same message. The
reason is in `docker compose logs ingest-worker`.

---

## 13. Shutting down

```bash
docker compose down          # stop everything, keep the database
docker compose down -v       # also delete the volumes — including the database
```

Stopping the *instance* costs nothing for compute, but note two consequences:

- the **public IP changes** on restart unless an Elastic IP is attached, and
- the tunnel URL changes regardless.

The S3 bucket and the SQS queues survive independently of the instance, so
recreating the box means [steps 3–9](#3-give-the-instance-permission-to-use-sqs)
again and nothing more. SQS at this volume is inside the permanent free tier —
a million requests a month — so leaving the queues in place between sessions
costs nothing.

---

## Where to go next

- `TUTORIAL.md` — why the system is built this way, in order
- `README.md` — what changed from version 1
- `V3_DESIGN.html` — the managed-infrastructure step after this one, and its cost
