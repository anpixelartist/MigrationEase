# Ledgerbridge frontend (React + TypeScript + Vite)

The import wizard. Talks to the FastAPI backend over `/api` (Vite proxies that to the backend on
:8000 in dev). Reproduces the design in `design-reference/` (the original prototype).

## Run

```bash
npm install
npm run dev        # http://localhost:5173  (proxies /api -> http://127.0.0.1:8000)
npm run build      # type-check + production build -> dist/
npm run preview    # serve the production build
```

Point the dev proxy elsewhere with `TM_BACKEND=http://host:port npm run dev`. In production, build
and serve `dist/` behind a server that proxies `/api` to the backend (or set `VITE_API_BASE`).

## Structure

```
src/
  api/         client.ts (typed fetch client: auth header, problem+json, task polling) + types.ts
  auth/        AuthContext (token in localStorage; signup/login/me)
  components/  ui.tsx (Button, Spinner, Toast)
  pages/       Login · Importer (the 6-step wizard) · BridgeSettings
  theme.ts     design tokens (colors/fonts from design-reference/)
design-reference/  the original .dc.html prototype — visual source of truth (not built)
```

## Wizard ↔ backend

Upload → `POST /jobs` + `/file`; Preview → `/profile`; Map → `/mapping/suggestions` + `/mapping`;
Validate/Plan/Import → `/validate`, `/generate`, `/push` (each returns a `task_id`, polled via
`/jobs/{id}/tasks/{task_id}`); Download → `/jobs/{id}/artifact`; Bridge tab → `/bridges` + `/bridge/status`.
