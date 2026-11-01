# PhotoSift mock app

A browser-only photo-library review prototype. All records are deterministic fake data generated locally; there is no backend and no real file access. User decisions are held in browser memory for the active session.

## Requirements

- Node.js 20.19+ or 22.12+
- pnpm 11.25.0 (pinned in `package.json`)

## Run

```bash
pnpm install
pnpm dev
```

Vite listens on `http://localhost:3000`. The static route manifest is served at `/manus-routes.json`.

## Checks and production build

```bash
pnpm typecheck
pnpm test
pnpm build
```

The core implementation lives in `src/domain.ts` (seeded data and aggregates), `src/store.ts` (decisions, selection, conflicts and undo), `src/components.tsx`, `src/screens.tsx`, and `src/router.tsx`. `public/photosift-mark.svg` is the site icon. The attached product specification is summarized in `plan.md` and its implementation outcomes are tracked in `TODO.md`.
