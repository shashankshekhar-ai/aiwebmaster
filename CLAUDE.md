# AIwebmaster — pre-flight checklist

Read `HANDOFF.md` first for full context. This file is just the checklist
that prevents repeat mistakes from past sessions — check it before and
after touching this app.

## Before claiming a change works

1. **This container is NOT bind-mounted** — code is `COPY`'d at build time.
   After editing anything under `apps/aiwebmaster/`, you MUST
   `docker compose -p rewamped-site build aiwebmaster` before
   `up -d --force-recreate aiwebmaster`, or you're testing stale code.
   Verify: `docker exec rewamped-site-aiwebmaster-1 grep -n "<new symbol>" /app/<file>`.
2. **`-p rewamped-site` is required** on every `docker compose` call from
   inside this container or from a bind-mounted context — omitting it
   silently targets zero containers instead of erroring.
3. **New DB table? Don't trust "no exception in logs."** Confirm the table
   actually exists: `docker exec rewamped-site-postgres-1 psql -U tbg -d tbg_api -c "\d <table>"`.
   Startup init functions are wrapped in try/except-log, so a real failure
   can look identical to success at a glance.
4. **New permission/action type → two places, not one.** Server-side gate
   is `auth/permissions.py` `ROLE_PERMISSIONS`; client-side gate (for
   showing/hiding buttons) is `static/assets/sidebar.js` `ROLE_PERMISSIONS`
   `canRun()`. Adding to only one leaves a button visible-but-403 or a
   working endpoint with no UI path to it.
5. **Don't guess routes — grep them.** `grep -n "@router\." <router file>`
   before assuming a path/prefix (e.g. login is `/api/login`, not
   `/api/auth/login`).
6. **Hit the real endpoint with a real session** before calling something
   done: log in via `POST /api/login` with `AIWEBMASTER_ADMIN_PASSWORD`
   from root `.env`, save cookies, curl the new route. A 200 in curl beats
   "the code looks right."
7. **`GEMINI_API_KEY` gotcha**: this host's shell exports a stale one in
   `~/.bashrc` that overrides `.env` on `docker compose up`. Use:
   `env -u GEMINI_API_KEY GEMINI_API_KEY="$(grep ^GEMINI_API_KEY= .env | cut -d= -f2-)" docker compose ...`

## After a meaningful change

- Update `HANDOFF.md` — what changed, what's verified vs. not, what's next.
  Don't let it drift out of date; it's the only "pick it up cold" doc.
- If it touches an executable action type, check whether both
  `core/aiwebmaster_agent.py` (the LLM-facing schema/prompt) and
  `core/executors.py` (the actual executor) need updating — they're
  separate and easy to update one without the other.
