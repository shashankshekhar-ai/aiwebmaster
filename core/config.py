from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Own login system (v2) — independent of Payload/Clerk (see D9 in
    # docs/planning/14_DECISION_LOG.md). Session cookies are signed with this.
    session_secret: str = ""
    # Seeds the first super_admin on startup if aiwebmaster_users is empty —
    # there's no public signup route, so someone has to exist first.
    aiwebmaster_admin_email: str = ""
    aiwebmaster_admin_password: str = ""

    # Auth0 ("Sign in with Auth0") — an alternate credential for an EXISTING
    # aiwebmaster_users row, not self-service signup: an unrecognized email
    # is rejected even with a fully valid Auth0 login. Unlike Google, Auth0
    # doesn't require HTTPS redirect URIs, so this works on a LAN IP too —
    # just register whatever auth0_redirect_uri is set to in the Auth0
    # Application's Allowed Callback URLs.
    auth0_domain: str = ""
    auth0_client_id: str = ""
    auth0_client_secret: str = ""
    auth0_redirect_uri: str = "http://localhost:8110/api/auth/auth0/callback"

    # Audit log lives in the api DB. SQL actions can target either DB by name.
    api_database_url: str = "postgresql://tbg:tbg_dev@postgres:5432/tbg_api"
    cms_database_url: str = "postgresql://tbg:tbg_dev@postgres:5432/tbg_cms"
    # Production stack DBs (docker-compose.prod.yml) — promoted into via "publish".
    api_database_url_prod: str = "postgresql://tbg:tbg_dev@postgres:5432/tbg_api_prod"
    cms_database_url_prod: str = "postgresql://tbg:tbg_dev@postgres:5432/tbg_cms_prod"

    ai_provider: str = "gemini"
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # Path to the repo inside this container (bind-mounted from the host) —
    # every git/docker executor runs with this as cwd.
    repo_path: str = "/repo"

    # The SAME repo, but as the real HOST filesystem path (not /repo, which
    # only means something inside this container's own mount namespace).
    # Needed specifically for `docker compose run` calls that use bind-mount
    # volumes (claude-agent/codex-agent) — those bind-mount sources get
    # resolved and applied by the HOST docker daemon (reached via the
    # mounted docker.sock), not by this container, so a relative or /repo-
    # relative path silently creates a new EMPTY directory on the host
    # instead of mounting the real one. Confirmed by testing: without
    # `--project-directory <this value>`, claude-agent saw genuinely empty
    # apps/* directories despite /repo (this container's own view) having
    # real content. Plain `docker compose build`/`up` (no bind-mount
    # volumes involved) don't need this — the CLI reads build context itself.
    host_repo_path: str = "/repo"

    # CMS base URL this service calls for content/nav actions.
    cms_url: str = "http://cms:3003"
    cms_service_token: str = ""
    # Dev web frontend — used only for post-nav_link live-render verification
    # (core/executors.py::call_nav_endpoint). Writing a nav row to the CMS
    # doesn't guarantee any component actually renders it (confirmed the hard
    # way: Footer.tsx silently ignored the whole navigation collection for
    # months) — this closes that gap by fetching the real page after a write
    # and checking the label actually appears, instead of reporting DB-write
    # success as if it were "the site changed."
    web_url: str = "http://web:3002"

    # Append-only audit trail (dual-written alongside the DB table) and
    # publish backups — both on a mounted volume so they survive redeploys.
    audit_log_path: str = "/app/data/audit.log"
    backups_dir: str = "/app/data/backups"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
