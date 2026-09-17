# Components 15–16: React shell and backend connection

> **Historical v2 walkthrough.** For the frontend in the complete local stack,
> use `make -C app infra-local` and open `http://localhost:5173`. The standalone
> Vite command below is only for frontend-focused development.

The frontend now has a buildable Vite/React shell and a connection screen.
`src/api.ts` owns the HTTP boundary; the component only manages form state and
presentation. The first connection check calls the backend liveness endpoint,
so it does not require credentials or a database.

Run it with:

```bash
cd app/frontend
npm install
npm run dev
```

Enter the local API URL and select **Connect backend**. A green indicator means
the API returned `{ "status": "ok" }`; errors remain local to the screen and
do not expose request details beyond the HTTP status.
