# Deployment: Render (API) + Vercel (UI)

Browser -> Vercel (static UI) -> `/api/*` rewrite -> Render (FastAPI).
The rewrite keeps UI and API on one origin: no CORS setup, frontend code unchanged.

## 0. Prerequisites
- Code pushed to GitHub (branch `main`).
- Free accounts on Render and Vercel.
- Add these files to the repo first: `requirements.txt`, `render.yaml`, `.env.example`, `samples/acme.txt` (repo root) and `frontend/vercel.json`.

## 1. Test locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd backend && python -m pytest -q && cd ..
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

## 2. Deploy API on Render
1. Render dashboard -> New -> Blueprint -> select the repo. Render reads `render.yaml`.
   Manual alternative: New -> Web Service, Python 3.12, build `pip install -r requirements.txt`, start `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check path `/healthz`.
2. Keep a single Uvicorn worker. Do not add `--workers`: job queue and locks are per-process.
3. Environment tab: confirm `APP_MODE=demo` and copy the generated `API_KEY`.
4. Deploy and wait for status Live.
5. Verify: `https://<service>.onrender.com/healthz` returns `{"status":"ok"}`; `/api/health` works with header `X-API-Key: <key>`.

Free-plan limits: the service sleeps when idle (first request is slow) and its disk is wiped on redeploy/restart, so uploaded documents and history reset. For persistence use a paid instance with a disk mounted at `/var/data` and set `DATA_DIR=/var/data`.

## 3. Deploy UI on Vercel
1. Edit `frontend/vercel.json`: replace `YOUR-SERVICE` with your Render service name. Commit and push.
2. Vercel -> Add New -> Project -> import the repo.
3. Settings:
   - Root Directory: `frontend`
   - Framework Preset: Vite
   - Build Command: `npm run build`
   - Output Directory: `dist`
   - Node.js Version: 22.x (package.json requires >=22.18)
4. Deploy.

## 4. Connect and verify
1. Open the Vercel URL. Header shows demo mode.
2. Open API access settings and paste the `API_KEY` from Render (kept in sessionStorage only).
3. Documents -> load demo samples -> wait for status `ready`.
4. Ask a question; check answer, claim audit, sources, graph.

## 5. Live mode (optional)
Needs Neo4j (for example AuraDB) and an OpenAI-compatible provider. On Render set `APP_MODE=live`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `LLM_BASE_URL`, `LLM_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, then redeploy. A bad config shows `degraded` on `/api/health`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Render build cannot find requirements | `requirements.txt` missing from repo root |
| UI "Cannot reach the API" | Wrong Render URL in `vercel.json`, or service asleep: retry after about a minute |
| 401 on every call | Paste the Render `API_KEY` in UI settings |
| Seed returns 404 | `samples/` folder missing from repo |
| Large upload fails | Vercel may cap proxied request size; test a small file first |
| Documents vanish | Free disk is ephemeral; use paid disk + `DATA_DIR` |
