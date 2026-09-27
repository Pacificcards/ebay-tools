"""
Run headless Claude Code (`claude -p`) as a structured-output function.

Auth is the user's Claude subscription via CLAUDE_CODE_OAUTH_TOKEN (CI) or the local
`claude` login. The subprocess gets a stripped environment on purpose:
  - ANTHROPIC_API_KEY is never passed, so usage can't silently bill API credits.
  - Google / Supabase secrets are never visible to a model that reads untrusted email.
It also runs in an empty temp dir so no CLAUDE.md or project settings are loaded, with
session persistence off so no transcript of email text is written under ~/.claude.
"""

import json
import os
import subprocess
import tempfile

import config

_PASSTHROUGH_ENV = ("PATH", "HOME", "USER", "LOGNAME", "CLAUDE_CODE_OAUTH_TOKEN")


class ClaudeError(RuntimeError):
    pass


def run(prompt: str, schema: dict, model: str, tools: str = "", stdin: str = "",
        max_turns: int | None = None) -> dict:
    """Run one prompt and return the schema-validated `structured_output` dict."""
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--tools", tools,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--no-session-persistence",   # never save a transcript (it would contain email text)
        "--strict-mcp-config",        # ignore any MCP servers configured on this machine
    ]
    if tools:
        cmd += ["--allowedTools", tools]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]

    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True, env=env, cwd=workdir,
                timeout=config.CLAUDE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"claude timed out after {config.CLAUDE_TIMEOUT_SECONDS}s")
        except OSError as exc:        # e.g. claude not installed
            raise ClaudeError(f"could not start claude: {exc}")
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    if out.get("is_error") or out.get("structured_output") is None:
        raise ClaudeError(f"claude error: {str(out.get('result'))[:500]}")
    return out["structured_output"]
