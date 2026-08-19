"""
Streams a single turn of an interactive Agent Terminal session — spawns
`docker compose run --rm <claude-agent|codex-agent> "<prompt>"`, yields each
line of output as it arrives (not buffered until exit, unlike
core/executors.py::run_codegen_agent's one-shot blocking version used by the
chat-proposed `codegen_agent` action), then yields a diff + done event once
the sandbox container exits. First asyncio-subprocess use in this app —
everything else here is synchronous subprocess.run.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from core.codegen_router import route_codegen
from core.config import settings
from core.executors import _run_subprocess
from db.agent_sessions import add_event, set_cli_session_id, set_session_tool

_CODEGEN_SANDBOXES = {"claude": "claude-agent", "codex": "codex-agent"}
# Field holding the CLI's own session id, keyed by tool — used to populate
# $RESUME_ID on the next turn so a follow-up prompt continues the same
# conversation instead of starting fresh. Different per CLI (confirmed by
# running each sandbox once): Claude Code's stream-json emits "session_id"
# on its init/result events, Codex's emits "thread_id" on "thread.started".
_SESSION_ID_FIELDS = {"claude": "session_id", "codex": "thread_id"}

_RUNNING: dict[int, "asyncio.subprocess.Process"] = {}


def _extract_session_id(tool: str, line: str) -> str | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    return event.get(_SESSION_ID_FIELDS[tool])


def stop_session(session_id: int) -> None:
    proc = _RUNNING.get(session_id)
    if proc and proc.returncode is None:
        proc.terminate()


async def run_agent_turn_stream(session: dict[str, Any], prompt: str) -> AsyncIterator[dict[str, Any]]:
    tool = session.get("tool")
    if not tool:
        tool = await asyncio.to_thread(route_codegen, prompt)
        await asyncio.to_thread(set_session_tool, session["id"], tool)
        session["tool"] = tool
    service = _CODEGEN_SANDBOXES[tool]

    resume_id = session.get("cli_session_id") or ""
    cmd = [
        "docker", "compose", "-f", f"{settings.repo_path}/docker-compose.yml",
        "--project-directory", settings.host_repo_path,
        "-p", settings.compose_project, "run", "--rm",
        "-e", f"RESUME_ID={resume_id}",
        service, prompt,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=settings.repo_path,
    )
    _RUNNING[session["id"]] = proc

    try:
        pending_session_id = None
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            if pending_session_id is None:
                pending_session_id = _extract_session_id(tool, line)
            event = {"type": "output", "data": line}
            await asyncio.to_thread(add_event, session["id"], "output", line)
            yield event

        returncode = await proc.wait()
        if returncode != 0:
            # Don't persist a session id captured from a turn that ultimately
            # failed (e.g. auth error mid-turn) — resuming a non-conversation
            # on the next turn fails outright ("No conversation found"),
            # confirmed by testing. Only a clean exit is a real resumable id.
            error_event = {"type": "error", "data": f"sandbox exited with code {returncode}"}
            await asyncio.to_thread(add_event, session["id"], "error", error_event["data"])
            yield error_event
        elif pending_session_id and pending_session_id != resume_id:
            await asyncio.to_thread(set_cli_session_id, session["id"], pending_session_id)
            session["cli_session_id"] = pending_session_id
    finally:
        _RUNNING.pop(session["id"], None)

    diff_stat = await asyncio.to_thread(_run_subprocess, ["git", "diff", "--stat"], cwd=settings.repo_path, timeout=30)
    diff = await asyncio.to_thread(_run_subprocess, ["git", "diff"], cwd=settings.repo_path, timeout=30)
    diff_text = diff["stdout"][-8000:]
    if diff_text.strip():
        diff_event = {"type": "diff", "data": diff_text, "diff_stat": diff_stat["stdout"], "tool": tool}
        await asyncio.to_thread(add_event, session["id"], "diff", diff_text)
        yield diff_event

    done_event = {"type": "done", "data": ""}
    await asyncio.to_thread(add_event, session["id"], "done", "")
    yield done_event
