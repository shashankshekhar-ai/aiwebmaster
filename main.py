import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from auth.bootstrap import bootstrap_admin
from auth.router import router as auth_router
from core.ai_settings import init_ai_settings_table
from db.agent_sessions import init_agent_tables
from db.ai_usage import init_ai_usage_table
from db.audit import init_audit_table
from db.chat_sessions import init_chat_tables
from db.deploy_state import init_deploy_state_table
from routers import actions, agent, browse, chat, deploy, files, git, settings, system, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="AIwebmaster", version="0.2.0", docs_url="/api/docs")


@app.on_event("startup")
def on_startup() -> None:
    try:
        init_audit_table()
    except Exception:
        logger.exception("failed to init aiwebmaster_audit table")
    try:
        bootstrap_admin()
    except Exception:
        logger.exception("failed to bootstrap aiwebmaster_users")
    try:
        init_ai_settings_table()
    except Exception:
        logger.exception("failed to init aiwebmaster_ai_settings table")
    try:
        init_chat_tables()
    except Exception:
        logger.exception("failed to init chat session tables")
    try:
        init_deploy_state_table()
    except Exception:
        logger.exception("failed to init aiwebmaster_deploy_state table")
    try:
        init_agent_tables()
    except Exception:
        logger.exception("failed to init agent terminal session tables")
    try:
        init_ai_usage_table()
    except Exception:
        logger.exception("failed to init aiwebmaster_ai_usage table")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html", headers=_NO_CACHE_HEADERS)


@app.get("/login")
def login_page() -> FileResponse:
    return FileResponse("static/login.html", headers=_NO_CACHE_HEADERS)


@app.get("/browse")
def browse_page() -> FileResponse:
    return FileResponse("static/browse.html", headers=_NO_CACHE_HEADERS)


@app.get("/users")
def users_page() -> FileResponse:
    return FileResponse("static/users.html", headers=_NO_CACHE_HEADERS)


@app.get("/system")
def system_page() -> FileResponse:
    return FileResponse("static/system.html", headers=_NO_CACHE_HEADERS)


@app.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse("static/settings.html", headers=_NO_CACHE_HEADERS)


@app.get("/help")
def help_page() -> FileResponse:
    return FileResponse("static/help.html", headers=_NO_CACHE_HEADERS)


@app.get("/git")
def git_page() -> FileResponse:
    return FileResponse("static/git.html", headers=_NO_CACHE_HEADERS)


@app.get("/deploy")
def deploy_page() -> FileResponse:
    return FileResponse("static/deploy.html", headers=_NO_CACHE_HEADERS)


@app.get("/agent")
def agent_page() -> FileResponse:
    return FileResponse("static/agent.html", headers=_NO_CACHE_HEADERS)


app.include_router(auth_router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(browse.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(git.router, prefix="/api")
app.include_router(deploy.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(agent.ws_router, prefix="/api")

app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")
