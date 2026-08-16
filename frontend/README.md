# Frontend — Freigabe-Dashboard

Next.js (App Router, TypeScript) dashboard for the Mail-Assistent MVP. Talks
to the FastAPI backend via `NEXT_PUBLIC_API_BASE_URL` (see
`.env.local.example`).

See the [repository README](../README.md) for full setup instructions
(backend, database, queue, Gmail OAuth).

## Local development

```bash
npm install
cp .env.local.example .env.local
npm run dev
```

## Scripts

- `npm run dev` — start the dev server (http://localhost:3000)
- `npm run build` — production build
- `npm run lint` — ESLint
