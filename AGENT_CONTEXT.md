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

## Operational facts learned from real end-to-end testing (this session)

- Domain migrated `.com` -> `.net` (`thebradburygroup.net`). Public
  aiwebmaster URL: `https://webmaster.thebradburygroup.net`.
- Docker compose project name is `tbz`, not `rewamped-site` — always
  `docker compose -p tbz ...`. aiwebmaster itself is bind-mounted at
  `127.0.0.1:8001` (not 8110 — that port number appears in older docs,
  outdated).
- Chat's `claude_cli` provider must run `model: sonnet`, not `haiku` —
  confirmed by direct testing that haiku frequently narrates a plan in
  plain text instead of emitting the JSON `actions` array, even though the
  system prompt requires JSON-only output. Root cause was actually
  `infra/claude-agent/run.sh`'s `QUERY_MODE=1` block running with
  `--permission-mode plan` (fixed — now `bypassPermissions`, since that
  call exposes no file/bash tools to gate in the first place), but keep
  `sonnet` as the configured model regardless — haiku is measurably less
  reliable at the JSON contract even after that fix.
- `codex-agent` sandbox is not logged in (confirmed) but `core/
  codegen_router.py::route_codegen` always returns `"claude"` regardless
  of input — this is dead code, not a live gap. Don't waste time getting
  codex-agent logged in unless the router is deliberately changed back to
  per-task routing.
- `user_management` action type now supports `op: "set_enabled"` (payload
  `{user_id, enabled}`) and `op: "reset_password"` (payload `{user_id,
  password}`), in addition to the original upsert-by-email shape (no
  `op`, or `op: "upsert"`). Both new ops refuse a `user_id` matching the
  acting user's own id (checked in `routers/actions.py` against the real
  session, not client input) — a super_admin can't disable or reset their
  own account this way. Disabling immediately bumps `session_epoch`,
  killing any live session for that account (confirmed).
- `aiwebmaster_audit`'s stored `payload` redacts the `password` key
  (`routers/actions.py`, since <this session>) — never assume a password
  is recoverable from audit history, and never propose an `sql` action
  that would try to read one back out of it (it's an intentional dead
  end).
- Media upload (`media` action) failed for real with `EACCES: permission
  denied, mkdir 'media'` from the cms container — root cause was
  `apps/cms/Dockerfile`'s `WORKDIR` being root-owned with no `--chown` on
  the directory itself (only on files explicitly `COPY`'d in), so the
  non-root `payload` user could write into existing folders but not
  create a new one at runtime. Fixed by creating+chowning `media/` in the
  Dockerfile before `USER payload`. If a similar "works for existing
  files, fails to create something new" error shows up in `web` or `api`
  containers, check the same class of bug there first.

- `content` action now supports `delete: true` (payload {kind, docId, delete: true}) for page/post/resource/case-study/faq/testimonial — was previously only removable via raw SQL. Permanent, no soft-delete.
- Fixed: `publish:true` on a `content` action for post/resource/case-study
  wasn't actually making it live — the collection's own `status` select
  field (what apps/web's queries actually filter on) was disconnected
  from the `publish` flag (which only touched Payload's own internal
  draft/version state, never checked by the public site), and a second
  bug in the create path unconditionally reset status back to "draft"
  regardless. Both fixed in `apps/cms/src/endpoints/contentAgent.ts`.
  If a future "I published it but it's not showing" report comes in for
  ANY content kind, check this exact class of bug (status field vs.
  Payload draft state) before assuming it's something else.

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
