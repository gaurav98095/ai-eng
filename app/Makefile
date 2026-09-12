# Everything here is a docker compose command underneath.

.PHONY: up down logs ps build url queues ready shell-db scale-ingest clean

up:              ## build and start everything
	docker compose up -d --build
	@echo; $(MAKE) --no-print-directory url

down:
	docker compose down

clean:           ## also delete the volumes — including the database
	docker compose down -v

build:
	docker compose build

logs:
	docker compose logs -f

ps:
	docker compose ps

url:             ## the public address the tunnel handed out
	@docker compose logs cloudflared 2>/dev/null \
	  | grep -oh 'https://[-a-z0-9]*\.trycloudflare\.com' | tail -1 \
	  || echo "no tunnel url yet — try: docker compose logs cloudflared"

queues:          ## how far behind are we
	@curl -s localhost:$${WEB_PORT:-8080}/api/metrics/queues | python3 -m json.tool

ready:           ## is every dependency reachable
	@curl -s localhost:$${WEB_PORT:-8080}/api/ready | python3 -m json.tool

scale-ingest:    ## more ingest workers: make scale-ingest N=3
	docker compose up -d --scale ingest-worker=$(or $(N),3)

shell-db:        ## open the sqlite file
	docker compose exec api python -c "import sqlite3,sys; \
	  [print(r) for r in sqlite3.connect('/data/edgentrag.db').execute( \
	  'select name from sqlite_master where type=\"table\"')]"
