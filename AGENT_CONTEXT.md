# AIwebmaster site knowledge

Static reference loaded into every chat turn (see `core/context.py`), on top of
the live snapshot (current pages/posts/nav/docker status). Keep this short —
it's injected into every request.

## Stack

- Monorepo (pnpm workspaces): `apps/web` (Next.js 15 App Router, public site),
  `apps/cms` (Payload CMS, Postgres-backed, port 3003), `apps/api` (FastAPI,
  leads/assessment/integrations, port 8000), `apps/aiwebmaster` (this agent).
- Postgres: one instance, two databases — `tbg_cms` (Payload) and `tbg_api`
  (FastAPI + this agent's own tables). Shared `tbg` role, dev creds.
- Deploy: `docker compose build <service>` then
  `docker compose up -d --force-recreate <service>`. No CI/CD — a git push
  triggers nothing downstream. Rebuilding is required after any source change;
  restarting alone does not pick up new code (Next.js/Payload are compiled at
  build time).

## Content model (CMS collections)

- `pages` — flexible block-based pages (hero, richText, cardGrid, ctaBanner).
- `posts` — Insights articles: title/slug/excerpt/content/category/
  contentType/author group/seo group (incl. `aiSummary` for GEO)/featured/
  readingTime. Rendered at `/insights` and `/insights/[slug]`.
- `resources`, `case-studies`, `faqs`, `testimonials` — similar shape.
- `navigation` — drives the actual header/footer nav (NOT the hardcoded
  fallback array in `Header.tsx` — that's a fallback only used if the CMS
  query returns zero rows). Adding a page without a matching `navigation` row
  means it's unreachable from the UI even though it renders fine directly.

## Brand / design conventions (for `content` actions)

Navy `#0c2940`, teal/slate `#39918d`/`#3f6d67`, terracotta `#c57b4b`, gold
`#f8c51c`, ink neutrals `#D9E3E6`/`#60707A`. Font utility classes:
`font-montserrat`, `font-inter`, `font-roboto`, `font-h1`/`font-h2`/`font-h3`.
No marketing clichés, no fabricated stats/testimonials/credentials in any
proposed copy — this firm's tone is confident and concrete, not salesy.

## Safety rules (non-negotiable, independent of role permissions)

- Never propose or run unscoped `DROP`/`TRUNCATE`/`DELETE` without a `WHERE`
  clause (also enforced in code, `core/executors.run_sql`).
- `nginx` and `system` (OS package) actions are always draft-only — explain
  the command, never execute it, regardless of who's asking. The host runs
  60+ unrelated containers; an OS-level change here is genuinely double-edged.
- Every action is propose-then-approve. Never claim something is done before
  a human clicks Run and it actually succeeds.
- `docker` actions only ever target `cms`, `web`, `api` — never `postgres`,
  and never touch containers outside this compose project.
- `code_edit` (infra_admin/super_admin only) refuses `.env` and `.git/` paths
  even with approval, and refuses any path resolving outside the repo root.
  Its `mode: "edit"` requires the exact current text (`old_string`) to match
  exactly once — you have no file-read tool of your own, so if you don't
  already know a file's real content, ask the human to `/read` it into the
  conversation first rather than guessing.

## Known unwired areas (as of this session)

- The header logo (`apps/web/components/layout/HeaderNav.tsx`) is a hardcoded
  `<Image src="/brand/White-Monochrome-Text.png">`, NOT read from
  `SiteSettings.logo` in the CMS (that field exists but nothing renders it).
  Changing the logo today means a `code_edit` to that file, not a `content` action.
- `/contact` (`apps/web/app/contact/page.tsx`) is a hardcoded Next.js route,
  not a CMS `pages` doc — same limitation, needs `code_edit` not `content`.
- The homepage (`/`, `apps/web/app/page.tsx`) is ENTIRELY hardcoded — hero,
  carousel, three paths, "How It's Different" cards, closing CTA are all
  literal arrays/JSX in that file and `apps/web/components/home/*.tsx`, not
  read from any CMS collection. A `pages` doc with slug "home" may exist in
  the CMS (id 5, leftover from early testing) — it renders nowhere; a
  `content` action against it always reports success but is never visible
  live, confirmed repeatedly. ANY request to change something on the
  homepage — a card, headline, CTA text, anything — must be a `code_edit` or
  `codegen_agent` action against these files, never a `content` action with
  kind:"page" and slug "home". If genuinely unsure which file/array holds
  the text in question, use `codegen_agent` rather than guessing.
