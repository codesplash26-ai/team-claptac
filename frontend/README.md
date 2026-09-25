# ATLAS Agent Manager (frontend)

React + TypeScript + Vite. The production build is committed to `atlas/dashboard/static/manager/`, so the API
serves it at `/manager/` and nobody needs Node just to run ATLAS.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies the API on :8000
npm run build      # type-checks, then writes atlas/dashboard/static/manager/  (commit the result)
```

Start the API with `uvicorn atlas.api.main:app --reload`.
