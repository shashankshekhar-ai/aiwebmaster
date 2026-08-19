# AIwebmaster — handoff

Standalone ops+content agent for The Bradbury Group's site (`apps/aiwebmaster`,
FastAPI + vanilla HTML/Tailwind-CDN, no build step). Full design history is in
`docs/planning/14_DECISION_LOG.md` (search "D9" — v1 through v6 addenda,
chronological). This file is the "pick it up cold" summary.

## What it does

Chat (or Browse picker) → agent proposes typed **actions** → human clicks
**Run** on each one individually → executor runs it → logged to an
append-only audit trail. Nothing executes without an explicit click, ever.

Action types: `content` (Pages/Posts/Resources/Case Studies, draft-by-default),
`nav_link` (add/update/remove/reorder header+footer nav), `git` (commit/push,
or discard uncommitted changes), `docker` (start/stop/restart/rebuild, dev or
staging), `sql`, `code_edit` (mechanical old_string/new_string file edit),
`codegen_agent` (hands a task to a real coding agent — Claude Code or Codex
CLI, running in an isolated sandbox with its own file-edit+bash access —
instead of a hand-guessed diff; see below), `publish` (dev→staging DB
snapshot + redeploy, auto-backup first), `rollback` (restore latest staging
backup), `user_management`. `nginx`/`system` are draft-only — explained,
never executed, on purpose (shared host, too risky).

### `codegen_agent` — real coding agents, not hand-guessed diffs

`code_edit` asks the chat LLM (often gemini-flash-latest — fast/cheap, not a
model you'd trust with a whole-file rewrite) to hand-write an exact
`old_string`/`new_string` match with no file-reading tool of its own. For
anything beyond a trivial single-line change, `codegen_agent` is the better
path: it shells out to one of two isolated sandbox containers —
`infra/claude-agent` (Claude Code CLI) or `infra/codex-agent` (OpenAI Codex
CLI), both bind-mounting the same dev `apps/web|cms|api|aiwebmaster` source
directories aiwebmaster itself sees — with full file-edit + bash tool
access, no docker socket, no `.env`, no `.git`, no route to prod. Which
sandbox handles a given request is picked by `core/codegen_router.py`, a
small dedicated LLM call separate from the main chat conversation (not the
chat model's own judgement) — the executor (`core/executors.py::run_codegen_agent`)
runs `docker compose -p rewamped-site run --rm <claude-agent|codex-agent> "<prompt>"`
synchronously (can take several minutes — no async/job infra in this app,
matches every other long-running action here), then `git diff`s the working
tree afterward and returns that as the review artifact. No auto-commit — the
existing `git` action and Git page are still the commit/discard path.

Both sandboxes use browser/device-code login against an existing Claude/
ChatGPT subscription (`docker compose run --rm --entrypoint claude
claude-agent` / `... --entrypoint codex codex-agent login --device-auth`),
not per-token API keys — session persists in `claude_agent_home`/
`codex_agent_home` volumes. **Not yet done as of this session: neither
sandbox has actually been logged into** — real prompts fail on "Not logged
in" / 401 until someone runs the login flow once.

**Battle-tested this session** (real end-to-end runs, not just import-checks):
the full pipeline — router picks a tool, `docker compose run --rm
<claude-agent|codex-agent> "<prompt>"` streams line-by-line, `git diff`
capture, DB persistence — was exercised for real against both sandboxes
via `/api/agent/ws/{id}` (see Agent Terminal below) and confirmed working
mechanically end to end; only the actual login step is outstanding. Two
real bugs found and fixed by this testing (not caught by import/syntax
checks alone): (1) `claude-agent`'s `run.sh` never set a permission mode,
so any real tool-use call would've hung forever with no TTY to answer the
approval prompt — fixed with `--permission-mode bypassPermissions` (which
in turn required switching the container off root, since Claude Code
refuses that flag as root — `claude-agent` now runs as `node:22-slim`'s
built-in uid 1000 `node` user, which also happens to match this host's
own uid so bind-mounted `apps/*` stay writable); (2) `--output-format
stream-json` in `--print` mode requires `--verbose` or the CLI exits
immediately with an argument error, undocumented in `--help`.

## Pages (sidebar)

Chat (`/`) · Browse & edit (`/browse`) · Git (`/git`) · Deploy (`/deploy`) ·
Agent Terminal (`/agent`) · Users (`/users`) · System (`/system`) ·
Settings (`/settings`, AI provider).

### Agent Terminal (`/agent`) — interactive streaming sandbox UI

Separate from `codegen_agent` (the chat-proposed, one-shot, blocking action
above): this page drives `claude-agent`/`codex-agent` directly, live,
multi-turn — the way this Claude Code session itself works, in a browser.
infra_admin/super_admin only (same `codegen_agent` RBAC slug — no new
permission type). New DB tables `aiwebmaster_agent_sessions`/
`aiwebmaster_agent_events` (`db/agent_sessions.py`) persist session history
so a reload replays it. `core/agent_stream.py` is this app's first
`asyncio.create_subprocess_exec` use — streams sandbox stdout line-by-line
over a new `/api/agent/ws/{id}` WebSocket (`routers/agent.py`) as it
arrives, rather than buffering until exit. Auth over the socket reuses the
same signed cookie as every other page (`auth/deps.py::require_session_ws`,
a WebSocket-safe variant of `require_session` — the two can't share one
FastAPI dependency, confirmed by testing: an `APIRouter(dependencies=...)`
requiring a `Request` crashes with a 500 when applied to a `@websocket`
route, so the WS route lives on its own `ws_router` with no router-level
dependency).

Multi-turn continuity: each session persists the sandbox CLI's own
conversation id (`cli_session_id` — Claude Code calls it `session_id`,
Codex calls it `thread_id`, different field names, handled per-tool in
`core/agent_stream.py::_SESSION_ID_FIELDS`) and passes it back via
`$RESUME_ID` on the next turn (`claude -p ... --resume <id>` /
`codex exec resume <id> ...`). Only persisted after a turn actually exits 0
— capturing it from a turn that error-exits and resuming a broken
conversation next time fails outright ("No conversation found"), confirmed
by testing during this session.

Stop button sends `{"stop": true}` over the socket, server-side
`proc.terminate()`s the tracked `docker compose run` process — **not yet
verified that `--rm` still cleans up the underlying container after
SIGTERM** (flagged as a follow-up check, not blocking).

### Real end-to-end test (Gallery page) — three more real bugs found and fixed

Drove Agent Terminal for real (logged-in `claude-agent`, real Claude Pro
account) to build a whole page, deploy it, commit, revert, and redo. Found
and fixed three more bugs no static check would have caught:

1. **Bind-mount path resolution from inside a container talking to the host
   docker.sock**: `docker compose run` (used by both `codegen_agent` and
   Agent Terminal) resolves its `volumes:` bind-mount *sources* on the HOST
   daemon side, not inside the calling container — so running it with
   `cwd=/repo` (this container's own bind-mounted view) silently created
   brand-new **empty** directories on the host instead of mounting the real
   `apps/web` etc. Fixed with `-f {repo_path}/docker-compose.yml
   --project-directory {settings.host_repo_path}` on both call sites
   (`core/executors.py::run_codegen_agent`, `core/agent_stream.py`) — new
   `HOST_REPO_PATH` env var (`core/config.py::settings.host_repo_path`)
   carries the real host path in explicitly, since a container can't
   discover its own bind-mount source path from the inside. Confirmed via
   direct `docker exec` testing before wiring it into the app.
2. **Claude Code account/OAuth state never actually persisted**: login
   "succeeded" every time but `claude auth status` still reported
   `loggedIn: false` on the next run. Root cause: Claude Code writes account
   state to `~/.claude.json` (a *file* directly in `$HOME`), separate from
   `~/.claude/` (a directory) — only the directory was volume-mounted
   (`claude_agent_home`). Fixed in `infra/claude-agent/run.sh` with a
   symlink (`~/.claude.json` → `~/.claude/.claude.json`) set up on every
   container start; also stopped recommending `--entrypoint claude` for the
   login command (bypassed `run.sh` entirely, skipping the symlink setup) —
   `docker compose run --rm claude-agent auth login` now works correctly
   because `run.sh` passes `auth`/`--help` straight through to the real CLI.
3. **`git` action failed outright with "Author identity unknown"**: the
   `aiwebmaster` container never had `git config user.name`/`user.email`
   set. Fixed in `apps/aiwebmaster/Dockerfile` — `git config --system
   user.name "AIwebmaster"` / `user.email
   "aiwebmaster@thebradburygroup.com"`. This is the git-level committer
   field only; the propose/approve/RBAC/audit trail is unaffected/unrelated.

All three confirmed fixed by re-running the exact failing scenario after
each fix — see `docs/planning/14_DECISION_LOG.md` v8 addendum for the full
build→deploy→commit→revert→redo trace.

### v9 — locked chat out of user_management, role-based nav filtering, friendlier UI

See `docs/planning/14_DECISION_LOG.md` v9 addendum for the full writeup.
Short version: the chat LLM can no longer produce `type: "user_management"`
in a proposal for any role (schema-level, not just RBAC) — the Users page's
own form still works unchanged, only the LLM's proposal path closed.
`code_edit` now also refuses `apps/aiwebmaster/auth/*`, and `sql` refuses
any statement touching `aiwebmaster_users` — defense in depth against a
technical role using a different action type to reach the same table.
`sidebar.js`'s `NAV_ITEMS` now hides links a role can't use (a `ui_editor`
sees only Chat + Browse). Action cards for `content`/`nav_link` show a
plain-English one-line summary with the raw JSON tucked behind a "Show
details" toggle; Agent Terminal's raw JSONL stream now surfaces just
assistant text + a compact "→ running: ToolName" line by default, collapsing
telemetry noise into small "…" toggles.

**codegen_agent/Agent Terminal residual risk, accepted not solved**: those
sandboxes still have full unrestricted file-edit access within their bind
mounts, including `apps/aiwebmaster/auth/` — no per-subpath mount ACL exists
to carve that out without also breaking their (real, used) ability to
improve AIwebmaster's own code generally. Mitigated by: already gated to
infra_admin/super_admin only; nav filtering keeps non-technical users off
this page entirely; any sandbox-made change still needs a separate `docker`
rebuild + `git` commit to take effect and be recorded, both independently
RBAC-gated and audit-logged.

**Not yet verified**: full chat round-trip test blocked by the configured
Gemini key being out of prepaid credits (account billing issue, unrelated to
this change) — verified the schema logic directly instead (`_proposable_types_for_role`
correctly excludes `user_management` for every role, confirmed via direct
import) and verified both blocklists (`sql`/`code_edit`) for real via
`curl`. Nav filtering verified by confirming the deployed `sidebar.js` has
the new `requires`/`filterNav` code live — not yet clicked through in an
actual browser as a non-`super_admin` user.

## Access right now

- URL: `http://192.168.0.122:8110` (LAN) or `http://localhost:8110` (host only)
- Login: `admin@thebradburygroup.com` / see `AIWEBMASTER_ADMIN_PASSWORD` in root `.env`
- Also: `sunnyrocks1122@gmail.com` (super_admin, via Auth0 — that's the user's real account)
- 4 roles: `docker_ops` (docker,git) · `ui_editor` (content,nav_link) ·
  `infra_admin` (+ sql,code_edit) · `super_admin` (everything)
- Auth0 wired and working (real tenant creds in `.env`); password login always
  available as fallback. CMS/Payload does NOT have Auth0 — explicitly deferred,
  tracked as a follow-up (see decision log v4 addendum).

## Environments

- **Dev** (what's normally being edited): `cms`:3003, `web`:3002, `api`:8000
- **Staging** (called "prod" internally in code/ports — same thing, renamed
  in UI only): `cms-prod`:3103, `web-prod`:3102, `api-prod`:8003 —
  `docker-compose.prod.yml` overlay, separate `tbg_cms_prod`/`tbg_api_prod` DBs
  on the same Postgres instance.
- Promote dev→staging: `publish` action (Deploy page or sidebar button).
  Auto-backs-up staging DB first; `rollback` restores latest backup.

## Known infra gotchas (already fixed, don't re-break)

1. **This host's shell has a stale `GEMINI_API_KEY` exported in `~/.bashrc`**
   (line ~121) that silently overrides `.env` on every `docker compose up`
   because Compose gives shell env vars priority over `.env` file values.
   Workaround used all session: `env -u GEMINI_API_KEY GEMINI_API_KEY="$(grep ^GEMINI_API_KEY= .env | cut -d= -f2-)" docker compose up -d --force-recreate aiwebmaster`.
   Real fix (not yet done): remove/fix the `~/.bashrc` line — user hasn't
   confirmed removal yet, ask before touching it.
2. **Debian trixie has no `docker-compose-plugin` or usable `docker.io` client
   package** — both `docker` CLI and the compose plugin are fetched as static
   binaries directly in `apps/aiwebmaster/Dockerfile` (see comments there).
3. **`docker compose` run from inside a bind-mounted container needs
   `-p rewamped-site` explicitly** — otherwise it silently finds zero
   containers (`working_dir` label mismatch between host path and `/repo`
   inside the container). Already applied everywhere in `core/executors.py`.
4. **Git needs `safe.directory /repo`** configured (baked into the
   Dockerfile) — without it every git command fails with "dubious ownership".
5. **pg_dump/pg_restore version mismatch** — trixie's `postgresql-client` is
   v17, our Postgres is v16. Fixed by using plain-SQL dump + `sed` to strip
   the one incompatible `SET transaction_timeout` line, instead of custom-format
   `pg_restore`. Applies to both `publish`'s main dump and its pre-publish backup.
6. **`git` action commits as root, leaving root-owned objects in the host's
   `.git/objects`** — this container runs as root and writes directly into
   the bind-mounted repo (including `.git`), so after any `git` action runs,
   host-side git commands can fail with "insufficient permission for adding
   an object to repository database" (hit this for real after the Gallery
   e2e test's commit). Not fixed structurally (would mean moving this
   container off root, which also touches its docker.sock access — real
   scope, not attempted). Workaround: `sudo chown -R $USER:$USER
   /path/to/repo/.git` on the host after any AIwebmaster-driven commit,
   before running git commands there yourself.

## Open bug — mid-investigation when this handoff was written

**User report**: "I can't see my own sent chat messages, only assistant
responses" (on the live Chat page, `sunnyrocks1122@gmail.com` account).

**Ruled out**: NOT a storage bug. Direct DB query confirms both `user` and
`assistant` rows are stored correctly, alternating in order, for that user's
session 1:
```sql
SELECT u.email, s.id, m.role, left(m.content,60) FROM aiwebmaster_chat_sessions s
JOIN aiwebmaster_users u ON u.id=s.user_id
JOIN aiwebmaster_chat_messages m ON m.session_id=s.id ORDER BY s.id, m.id;
```
This showed correct `user`/`assistant`/`user`/`assistant`... alternation with
real content in both roles. So the bug is client-side rendering, not the API
or DB.

**Not yet checked** (next steps for whoever picks this up):
- Open the Chat page as `sunnyrocks1122@gmail.com` in a real browser, DevTools
  open, and see whether user bubbles are (a) genuinely absent from the DOM,
  or (b) present but invisible (CSS issue — check `#log`'s `flex flex-col`
  + each row's `self-end`/`flex-row-reverse` classes in `static/index.html`'s
  `addMsg()`, and the teal bubble color `bg-brand-teal` against the navy bg).
- Check whether this happens on **live typing** vs. only on **reloading a
  session** (`loadSessionFromUrl()` in `static/index.html`) — both call the
  same `addMsg()`, so if one works and the other doesn't, the bug is in
  whichever code path isn't calling it, not in `addMsg()` itself.
- `addMsg('user', text)` call site confirmed still present (line ~323 of
  `static/index.html`) — wasn't accidentally deleted in a later edit.
- Worth a plain browser screenshot/inspect-element rather than more guessing
  from the server side — this needs eyes on the actual rendered page.

## Deploy/Rollback in the chat header, gated by an actual diff

Chat page header (`static/index.html`) now has both **Publish to staging**
and **Rollback staging** buttons (previously only Publish existed). Both
just call the existing `/api/actions/run` with `type: "publish"` /
`type: "rollback"` — same executor path as before (`core/executors.py`
`run_publish`/`run_rollback`), so nothing changed about what they *do*.

What's new is `GET /api/deploy/diff` (`routers/deploy.py`) — checked on page
load and again right after a publish completes:
- `code_changed`: compares current git HEAD sha + hash of `git diff HEAD`
  (`core/repo_state.py::git_state()`) against what was recorded at the last
  *successful* publish (`db/deploy_state.py`, table
  `aiwebmaster_deploy_state`, single row, written by `run_publish` on
  success). Catches both committed and uncommitted code changes since
  staging build copies the working tree.
- `content_changed`: dumps dev's and staging's CMS DB (`pg_dump` plain SQL)
  and compares hashes directly — no state tracking needed, self-correcting
  each check (`core/repo_state.py::db_content_hash()`).
- `has_backup`: whether a `publish` backup exists at all, for gating
  Rollback (reuses `core/executors.py::_backups_sorted()`).

Publish button disables with a tooltip when `has_changes` is false ("dev and
staging already match"). Rollback disables when `has_backup` is false. If
the diff fetch itself fails, buttons fail *open* (stay enabled) rather than
silently blocking a real publish/rollback on an unknown.

Not yet done: same diff-aware gating on the separate `/deploy` page (still
just start/stop/restart/rebuild per service, no Publish/Rollback there) —
was asked for "in chatbox only" this round, so left as-is. Also not
battle-tested end-to-end (no live publish run during this session — too
slow/disruptive to test blind); `GET /api/deploy/diff` itself confirmed
working via curl.

## Recently added, less thoroughly battle-tested

- Chat sessions (list/rename/delete, sidebar submenu) — DB-backed, works via
  curl tests, but see the open bug above for real-browser rendering.
- Shared in-app dialog (`dialogPrompt`/`dialogConfirm`/`dialogAlert` in
  `static/assets/sidebar.js`) replacing all native `prompt()`/`confirm()`/
  `alert()` — syntax-checked, not yet visually confirmed in a browser.
- Deploy page (`/deploy`) — start/stop/restart/rebuild per service, dev +
  staging, plus a recent-activity feed. Confirmed via curl (real stop/start
  cycle proven), not yet clicked through in a browser.
- Git page discard buttons (per-file + discard-all) — confirmed via curl.
- Sidebar "Git" badge showing live uncommitted-file count.
- `code_edit`'s follow-up "Rebuild `<service>` & Preview" button after a
  successful Run, auto-mapping file path → dev service.

## Not done / explicitly deferred

- CMS (Payload admin) has no Auth0 — password auth only there.
- No secrets vault — AI provider keys in Settings sit plaintext in `tbg_api`.
- No MFA on password login.
- `~/.bashrc`'s stale `GEMINI_API_KEY` export not removed (needs user's OK).
- No true 3rd environment tier — "staging" is the only promotion target,
  confirmed by user this is intentional (no real external prod exists yet).

## Everything is committed and pushed

Two commits this session: `7b500e7` (initial AIwebmaster build) and `48ae5c1`
(browser/device-code login for claude-agent). **Everything after that —
all of v2 through v6 (auth hardening, code_edit, UI rewrite, chat sessions,
Deploy page, discard, dialogs) — is uncommitted in the working tree.** Check
`git status` / the Git page's badge count before doing anything destructive.
