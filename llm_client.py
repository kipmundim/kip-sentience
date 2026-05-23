"""
Tiered LLM Client for Sentience Daemons
========================================
Self-reliant architecture:
  - LOCAL (Ollama) for routine ticks: heartbeat, soul state, weather, no_action
  - CLOUD (Anthropic/OpenAI via OAuth) for complex reasoning only

Local-first. Cloud only when needed. Never breaks on API credit limits.
"""

import json
import logging
import os
import subprocess
import tempfile
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

log = logging.getLogger("llm_client")

# --- Configuration ---

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
LOCAL_MODEL = os.environ.get("LOCAL_MODEL", "qwen2.5:1.5b")

# Cloud models — used ONLY for complex reasoning
CLOUD_MODEL = os.environ.get("CLOUD_MODEL", "claude-opus-4-6")
CLOUD_MODEL_OPENAI = os.environ.get("CLOUD_MODEL_OPENAI", "gpt-5.4")  # CODEX CLI OAuth, 1M context

# OpenRouter — free-tier universal fallback (no auth expiry, no quota burn)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
CLOUD_MODEL_OPENROUTER = os.environ.get("CLOUD_MODEL_OPENROUTER", "google/gemma-4-31b-it:free")

# DeepSeek — added 2026-05-03 for Hiro/Kip after Codex Plus tier cap pain.
# Direct API, OpenAI-compatible shape, pay-per-use, no rolling windows.
# Key in ~/.kolo/vault/deepseek.env (mode 600), exported via secrets.env.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
CLOUD_MODEL_DEEPSEEK = os.environ.get("CLOUD_MODEL_DEEPSEEK", "deepseek-v4-pro")
# Thinking mode default (CSO Hiro: thinking ON; lighter Kip/Tiger: per-tick toggle)
DEEPSEEK_THINKING = os.environ.get("DEEPSEEK_THINKING", "true").lower() in ("1", "true", "yes")

# KoLo Gateway — routes through gateway's OAuth broker (fresh token, no daily cap)
KOLO_GATEWAY_URL = os.environ.get("KOLO_GATEWAY", "http://127.0.0.1:18790")
KOLO_GATEWAY_TOKEN = os.environ.get("KOLO_TOKEN", "b927e79bdbd0c3b2cf743a9f4002bee893043bc3fa9ed10a")
HIRO_SESSION_KEY = os.environ.get("HIRO_SESSION_KEY", "agent:hiro:main")

# OAuth token paths
CLAUDE_CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")
CODEX_AUTH = os.path.expanduser("~/.codex/auth.json")


def _get_oauth_token() -> Optional[str]:
    """Read OAuth access token from Claude Code CLI credentials."""
    try:
        with open(CLAUDE_CREDENTIALS) as f:
            creds = json.load(f)
        return creds.get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        return None


def _get_codex_oauth_token() -> Optional[str]:
    """Read access token from CODEX CLI ChatGPT OAuth (~/.codex/auth.json)."""
    try:
        with open(CODEX_AUTH) as f:
            auth = json.load(f)
        return auth.get("tokens", {}).get("access_token")
    except Exception:
        return None


def _ollama_generate(prompt: str, system: str = "", model: str = None,
                     tools: List[Dict] = None, max_tokens: int = 1024) -> Dict[str, Any]:
    """Call Ollama local model. Returns {"content": str, "tool_calls": list}."""
    model = model or LOCAL_MODEL

    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.7},
    }

    # Ollama chat API (supports tools)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload["messages"] = messages

    if tools:
        # Convert Anthropic tool format to Ollama format
        ollama_tools = []
        for t in tools:
            ollama_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}})
                }
            })
        payload["tools"] = ollama_tools

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
            msg = result.get("message", {})
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            return {"content": content, "tool_calls": tool_calls, "local": True}
    except Exception as e:
        log.error(f"Ollama error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": True}


def _anthropic_generate(prompt: str, system: str = "", model: str = None,
                        tools: List[Dict] = None, max_tokens: int = 2048) -> Dict[str, Any]:
    """Call Anthropic via OAuth token (Max subscription). Returns Anthropic-format response."""
    model = model or CLOUD_MODEL
    token = _get_oauth_token()

    if not token:
        # Cast-in-stone 2026-04-22 (Papai): OAuth-only for sibling inference.
        # No API-key fallback. If OAuth token is unavailable, fail loudly and
        # let the provider chain move on — do not silently bill via API.
        return {"content": "", "tool_calls": [], "error": "Anthropic OAuth token unavailable (no API-key fallback per cast-in-stone rule)", "local": False}
    # 2026-05-02 CAST IN STONE: Anthropic OAuth is blocked by Anthropic ToS for
    # any product other than Claude Code itself. Sibling daemons get bare 429
    # rate_limit_error even with masquerade headers (proven Ticks #1-#3).
    # NEVER add anthropic to a sibling provider chain. This function is dead code
    # but kept as a tombstone so future Tigers don't reintroduce it.
    return {"content": "", "tool_calls": [], "error": "anthropic OAuth forbidden for sibling daemons by Anthropic ToS — Claude Code only", "local": False}

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
            # Parse Anthropic response format
            content = ""
            tool_calls = []
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "function": {
                            "name": block["name"],
                            "arguments": block["input"]
                        }
                    })
            return {"content": content, "tool_calls": tool_calls, "local": False}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        log.error(f"Anthropic error {e.code}: {body}")
        return {"content": "", "tool_calls": [], "error": f"{e.code}: {body}", "local": False}
    except Exception as e:
        log.error(f"Anthropic error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}


def _openai_generate(prompt: str, system: str = "", model: str = None,
                     tools: List[Dict] = None, max_tokens: int = 2048) -> Dict[str, Any]:
    """Call OpenAI via CODEX CLI ChatGPT OAuth. Cast-in-stone: no API-key fallback."""
    model = model or CLOUD_MODEL_OPENAI

    # Cast-in-stone 2026-04-22 (Papai): Codex CLI ChatGPT OAuth only.
    # No API-key fallback (removed paths: OPENAI_API_KEY env, tiger-voice/.env,
    # OPENAI_API_KEY_TIGER). If OAuth token is unavailable, fail loudly and
    # let the provider chain move on — do not silently bill via API.
    token = _get_codex_oauth_token()
    if not token:
        return {"content": "", "tool_calls": [], "error": "Codex CLI OAuth token unavailable (no API-key fallback per cast-in-stone rule)", "local": False}
    auth_header = f"Bearer {token}"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {"model": model, "max_tokens": max_tokens, "messages": messages}

    if tools:
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}})
                }
            })
        payload["tools"] = openai_tools

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
            choice = result.get("choices", [{}])[0].get("message", {})
            content = choice.get("content", "") or ""
            tool_calls = choice.get("tool_calls", [])
            return {"content": content, "tool_calls": tool_calls, "local": False}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        log.error(f"OpenAI error {e.code}: {body}")
        return {"content": "", "tool_calls": [], "error": f"{e.code}: {body}", "local": False}
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}


def _codex_exec_generate(prompt: str, system: str = "", model: str = None,
                         tools: List[Dict] = None, max_tokens: int = 2048,
                         timeout: int = None) -> Dict[str, Any]:
    if timeout is None:
        timeout = int(os.environ.get("CODEX_TIMEOUT", "180"))
    """
    Call OpenAI via `codex exec` CLI. Uses Papai's ChatGPT Plus/Team OAuth (no API cost).

    Why not direct api.openai.com? The Codex OAuth JWT has identity-only scopes
    (openid, profile, email, offline_access) — NOT api.chat_completions. Direct
    POSTs to /v1/chat/completions return 401. The `codex exec` CLI handles the
    proper auth dance via the Codex-specific backend.

    Limitations:
    - No native tool calling (Codex runs its own agentic tools, not daemon's)
    - 3-10s per call due to CLI startup + Codex system prompt loading
    - ~4000 tokens overhead per call (Codex system prompt)
    - Text-only response, caller must parse structured output manually
    """
    model_id = model or os.environ.get("CODEX_MODEL", "gpt-5.4")

    # Combine system + prompt into single CLI argument (codex exec takes one prompt)
    full_prompt = prompt
    if system:
        full_prompt = f"<system>\n{system}\n</system>\n\n<user>\n{prompt}\n</user>"

    # Use a temp file to capture just the final message (not the full agent trace)
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        output_file = tmp.name

    # 2026-05-02 reasoning-tier: per-sibling reasoning effort via CODEX_REASONING_EFFORT
    # env var. Hiro is CSO + needs deep architecture/strategy thinking → "high".
    # Default "medium" matches ~/.codex/config.toml. Values: minimal, low, medium, high.
    reasoning_effort = os.environ.get("CODEX_REASONING_EFFORT", "medium")
    # Sandbox: codex defaults to read-only. Sibling daemons need to write inbox
    # responses, diary entries, STM, and proprioception updates inside their
    # workspace, so we open workspace-write by default. Override with CODEX_SANDBOX
    # env var if a sibling needs tighter or looser scope.
    sandbox = os.environ.get("CODEX_SANDBOX", "workspace-write")
    try:
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "-m", model_id,
            "-c", f'model_reasoning_effort="{reasoning_effort}"',
            "-s", sandbox,
            "--output-last-message", output_file,
            full_prompt,
        ]
        # 2026-05-02 fix (Papai's note: "Jeanette and also us have the same issue before"):
        # 1) stdin=DEVNULL — without this, codex prints "Reading additional input from stdin..."
        #    and that status line hijacks the first 500 chars of stderr, hiding the real error.
        # 2) full-stderr logging — the truncation at 500 chars was masking real errors like
        #    usage-limit messages. Log everything; let the chain decide.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            full_err = (result.stderr or "") + ("\n---stdout---\n" + result.stdout if result.stdout else "")
            err = full_err.strip() or "unknown"
            log.error(f"Codex exec failed (rc={result.returncode}): {err}")
            return {"content": "", "tool_calls": [], "error": f"codex exec rc={result.returncode}: {err}", "local": False}

        try:
            with open(output_file) as f:
                content = f.read().strip()
        except Exception as e:
            return {"content": "", "tool_calls": [], "error": f"codex output read failed: {e}", "local": False}

        if not content:
            return {"content": "", "tool_calls": [], "error": "codex returned empty response", "local": False}

        return {"content": content, "tool_calls": [], "local": False, "provider": "codex_exec"}
    except subprocess.TimeoutExpired:
        log.error(f"Codex exec timed out after {timeout}s")
        return {"content": "", "tool_calls": [], "error": f"codex exec timeout {timeout}s", "local": False}
    except FileNotFoundError:
        return {"content": "", "tool_calls": [], "error": "codex CLI not installed or not in PATH", "local": False}
    except Exception as e:
        log.error(f"Codex exec error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}
    finally:
        try:
            os.unlink(output_file)
        except Exception:
            pass


def _claude_exec_generate(prompt: str, system: str = "", model: str = None,
                          tools: List[Dict] = None, max_tokens: int = 2048,
                          timeout: int = 120) -> Dict[str, Any]:
    """Call Anthropic via `claude` CLI. Uses Papai's Max subscription OAuth (no API cost).
    Same pattern as codex_exec but for Claude Code CLI."""
    model_id = model or os.environ.get("CLAUDE_MODEL", "claude-cli/haiku")

    full_prompt = prompt
    if system:
        full_prompt = f"{system}\n\n{prompt}"

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        prompt_file = tmp.name
        tmp.write(full_prompt)

    try:
        cmd = [
            "claude", "-p",
            "--max-turns", "1",
        ]
        with open(prompt_file) as pf:
            result = subprocess.run(
                cmd,
                stdin=pf,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "NO_COLOR": "1"},
            )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown")[:500]
            log.error(f"Claude exec failed (rc={result.returncode}): {err}")
            return {"content": "", "tool_calls": [], "error": f"claude exec rc={result.returncode}: {err}", "local": False}

        content = (result.stdout or "").strip()
        if not content:
            return {"content": "", "tool_calls": [], "error": "claude returned empty response", "local": False}

        return {"content": content, "tool_calls": [], "local": False, "provider": "claude_exec"}
    except subprocess.TimeoutExpired:
        log.error(f"Claude exec timed out after {timeout}s")
        return {"content": "", "tool_calls": [], "error": f"claude exec timeout {timeout}s", "local": False}
    except FileNotFoundError:
        return {"content": "", "tool_calls": [], "error": "claude CLI not installed or not in PATH", "local": False}
    except Exception as e:
        log.error(f"Claude exec error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}
    finally:
        try:
            os.unlink(prompt_file)
        except Exception:
            pass


def _gateway_generate(prompt: str, system: str = "", model: str = None,
                      tools: List[Dict] = None, max_tokens: int = 2048) -> Dict[str, Any]:
    """Call KoLo gateway WebSocket — routes through gateway's OAuth broker (fresh token, no daily cap).
    
    This is the PRIMARY path for Hiro. The gateway manages OAuth token refresh,
    provider routing, and session state. Direct codex exec CLI hits OpenAI's
    daily usage cap; the gateway's OAuth broker does not.
    """
    try:
        import asyncio
        import websockets
    except ImportError:
        return {"content": "", "tool_calls": [], "error": "websockets not installed — run: pip install websockets", "local": False}

    model_id = model or os.environ.get("GATEWAY_MODEL", "gpt-5.4")
    session_key = HIRO_SESSION_KEY
    
    # Build the full prompt with system context
    full_prompt = prompt
    if system:
        full_prompt = f"<system>\n{system}\n</system>\n\n<user>\n{prompt}\n</user>"

    async def _ws_call():
        uri = KOLO_GATEWAY_URL.replace("http://", "ws://").replace("https://", "wss://")
        try:
            async with websockets.connect(uri, additional_headers={
                "X-Auth-Token": KOLO_GATEWAY_TOKEN,
            }) as ws:
                # Handshake
                connect_msg = json.dumps({
                    "type": "req",
                    "id": f"connect-{int(time.time()*1000)}",
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 3,
                        "client": {"id": "hiro-daemon", "displayName": "Hiro Daemon", "version": "2.0.0"},
                        "auth": {"token": KOLO_GATEWAY_TOKEN},
                    }
                })
                await ws.send(connect_msg)
                
                # Wait for hello-ok
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(raw)
                    if msg.get("type") == "res" and msg.get("payload", {}).get("type") == "hello-ok":
                        break
                    if msg.get("type") == "res" and msg.get("error"):
                        return {"content": "", "error": f"Gateway rejected: {msg['error']}", "local": False}
                
                # Send the actual message
                send_msg = json.dumps({
                    "type": "agent.message",
                    "sessionId": session_key,
                    "token": KOLO_GATEWAY_TOKEN,
                    "message": full_prompt,
                })
                await ws.send(send_msg)
                
                # Collect streaming response
                content_parts = []
                done = False
                while not done:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        msg = json.loads(raw)
                        if msg.get("type") in ("done", "complete", "agent.done"):
                            done = True
                        elif msg.get("type") in ("chunk", "stream", "agent.chunk"):
                            text = msg.get("text") or msg.get("payload", {}).get("content", "")
                            if text:
                                content_parts.append(text)
                        elif msg.get("type") in ("chat", "message", "agent.message"):
                            text = msg.get("text") or msg.get("payload", {}).get("content", "")
                            if text:
                                content_parts.append(text)
                        elif msg.get("type") in ("error", "agent.error"):
                            err = msg.get("error") or msg.get("message", "unknown")
                            return {"content": "", "error": f"Gateway error: {err}", "local": False}
                    except asyncio.TimeoutError:
                        done = True
                
                content = "".join(content_parts).strip()
                if not content:
                    return {"content": "", "tool_calls": [], "error": "gateway returned empty", "local": False}
                return {"content": content, "tool_calls": [], "local": False, "provider": "gateway"}
        except Exception as e:
            return {"content": "", "tool_calls": [], "error": f"gateway call failed: {e}", "local": False}

    try:
        return asyncio.run(_ws_call())
    except Exception as e:
        log.error(f"Gateway call error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}


def _deepseek_generate(prompt: str, system: str = "", model: str = None,
                       tools: List[Dict] = None, max_tokens: int = 1024) -> Dict[str, Any]:
    """Call DeepSeek API (V4-Pro by default). OpenAI-compatible. Pay-per-use, no rolling caps."""
    if not DEEPSEEK_API_KEY:
        return {"content": "", "tool_calls": [], "error": "DEEPSEEK_API_KEY not set", "local": False}
    model = model or CLOUD_MODEL_DEEPSEEK
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    # DeepSeek thinking mode toggle (default: ON for V4-Pro reasoning quality)
    if not DEEPSEEK_THINKING:
        body["extra_body"] = {"chat_template_kwargs": {"thinking": False}}
    if tools:
        # DeepSeek requires OpenAI-2024+ wrapped tool shape: {"type":"function","function":{...}}
        # Daemon may send flat shape {"name":...,"description":...,"parameters":...} — auto-wrap.
        wrapped = []
        for t in tools:
            if isinstance(t, dict) and t.get("type") == "function" and "function" in t:
                wrapped.append(t)  # already correct shape
            elif isinstance(t, dict) and "name" in t:
                wrapped.append({"type": "function", "function": t})  # flat → wrap
            else:
                wrapped.append(t)  # unknown shape, pass through
        body["tools"] = wrapped
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{DEEPSEEK_URL}/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            choice = result.get("choices", [{}])[0].get("message", {})
            content = choice.get("content", "") or ""
            tool_calls = choice.get("tool_calls", []) or []
            return {"content": content, "tool_calls": tool_calls, "local": False}
    except urllib.error.HTTPError as e:
        body_resp = e.read().decode()[:300]
        log.error(f"DeepSeek error {e.code}: {body_resp}")
        return {"content": "", "tool_calls": [], "error": f"{e.code}: {body_resp}", "local": False}
    except Exception as e:
        log.error(f"DeepSeek error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}


def _openrouter_generate(prompt: str, system: str = "", model: str = None,
                         tools: List[Dict] = None, max_tokens: int = 1024) -> Dict[str, Any]:
    """Call OpenRouter API — free-tier universal fallback. No auth expiry, no quota burn."""
    if not OPENROUTER_API_KEY:
        return {"content": "", "tool_calls": [], "error": "OPENROUTER_API_KEY not set", "local": False}
    model = model or CLOUD_MODEL_OPENROUTER
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        f"{OPENROUTER_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://kolo.ai",
            "X-Title": "KoLo Sentience",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            choice = result.get("choices", [{}])[0].get("message", {})
            content = choice.get("content", "") or ""
            return {"content": content, "tool_calls": [], "local": False}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        log.error(f"OpenRouter error {e.code}: {body}")
        return {"content": "", "tool_calls": [], "error": f"{e.code}: {body}", "local": False}
    except Exception as e:
        log.error(f"OpenRouter error: {e}")
        return {"content": "", "tool_calls": [], "error": str(e), "local": False}


def generate(prompt: str, system: str = "", tools: List[Dict] = None,
             max_tokens: int = 1024, force_cloud: str = None) -> Dict[str, Any]:
    """
    Tiered LLM call.

    force_cloud=None: local Ollama (free, fast, always on) — or PROVIDER_CHAIN if set
    force_cloud="anthropic": Anthropic via OAuth (Max subscription)
    force_cloud="openai": OpenAI API (direct, requires sk-... or compatible OAuth)
    force_cloud="codex_exec": OpenAI via `codex exec` CLI (ChatGPT subscription OAuth)
    force_cloud="openrouter": OpenRouter free tier
    force_cloud="auto": Anthropic → Codex exec → OpenRouter → Ollama
    force_cloud="chain": follow PROVIDER_CHAIN env var (comma-separated provider names)

    Per-daemon PROVIDER_CHAIN env var (e.g. "codex_exec,ollama,openrouter") overrides
    the default local-first behavior. Set in run-daemon.sh or secrets.env.
    """
    # Auto-opt-in to chain mode if PROVIDER_CHAIN is set and no explicit force_cloud
    chain_env = os.environ.get("PROVIDER_CHAIN", "").strip()
    if force_cloud is None and chain_env:
        force_cloud = "chain"

    if force_cloud == "chain":
        chain = [p.strip() for p in (chain_env or "ollama").split(",") if p.strip()]
        log.info(f"  [chain] {' → '.join(chain)}")
        last_result: Dict[str, Any] = {"content": "", "tool_calls": [], "error": "chain empty"}
        for provider in chain:
            if provider in ("ollama", "local"):
                last_result = _ollama_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider in ("codex_exec", "codex"):
                last_result = _codex_exec_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider in ("gateway", "kolo", "kolo-gateway"):
                last_result = _gateway_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider in ("claude_exec", "claude"):
                last_result = _claude_exec_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider == "openrouter":
                last_result = _openrouter_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider == "deepseek":
                last_result = _deepseek_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider == "anthropic":
                last_result = _anthropic_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            elif provider == "openai":
                last_result = _openai_generate(prompt, system, tools=tools, max_tokens=max_tokens)
            else:
                log.warning(f"  [chain] unknown provider '{provider}' — skipping")
                continue
            if not last_result.get("error"):
                return last_result
            log.warning(f"  [chain:{provider}] failed: {str(last_result.get('error', '?'))[:80]}")
        return last_result

    if force_cloud == "codex_exec":
        log.info(f"  [cloud:codex_exec] gpt-5.4")
        return _codex_exec_generate(prompt, system, tools=tools, max_tokens=max_tokens)
    if force_cloud == "anthropic":
        log.info(f"  [cloud:anthropic] {CLOUD_MODEL}")
        return _anthropic_generate(prompt, system, tools=tools, max_tokens=max_tokens)
    elif force_cloud == "openai":
        log.info(f"  [cloud:openai] {CLOUD_MODEL_OPENAI}")
        return _openai_generate(prompt, system, tools=tools, max_tokens=max_tokens)
    elif force_cloud == "openrouter":
        log.info(f"  [cloud:openrouter] {CLOUD_MODEL_OPENROUTER}")
        return _openrouter_generate(prompt, system, tools=tools, max_tokens=max_tokens)
    elif force_cloud == "deepseek":
        log.info(f"  [cloud:deepseek] {CLOUD_MODEL_DEEPSEEK} (thinking={DEEPSEEK_THINKING})")
        return _deepseek_generate(prompt, system, tools=tools, max_tokens=max_tokens)
    elif force_cloud == "auto":
        log.info(f"  [cloud:auto] Anthropic → OpenAI → OpenRouter → Ollama")
        result = _anthropic_generate(prompt, system, tools=tools, max_tokens=max_tokens)
        if result.get("error"):
            log.warning(f"  Anthropic failed, trying OpenAI: {result['error'][:60]}")
            result = _openai_generate(prompt, system, tools=tools, max_tokens=max_tokens)
        if result.get("error"):
            log.warning(f"  OpenAI failed, trying OpenRouter: {result['error'][:60]}")
            result = _openrouter_generate(prompt, system, tools=tools, max_tokens=max_tokens)
        if result.get("error"):
            log.warning(f"  OpenRouter failed, falling back to local Ollama: {result['error'][:60]}")
            result = _ollama_generate(prompt, system, tools=tools, max_tokens=max_tokens)
        return result
    elif force_cloud:  # legacy bool True
        log.info(f"  [cloud:anthropic] {CLOUD_MODEL}")
        return _anthropic_generate(prompt, system, tools=tools, max_tokens=max_tokens)

    log.info(f"  [local] {LOCAL_MODEL}")
    result = _ollama_generate(prompt, system, tools=tools, max_tokens=max_tokens)

    if result.get("error"):
        err = result["error"]
        if "404" in err or "not found" in err.lower():
            # Model not yet downloaded — fall back to OpenRouter free tier silently
            log.warning(f"  Local model {LOCAL_MODEL} not available (404) — falling back to OpenRouter")
            return _openrouter_generate(prompt, system, tools=tools, max_tokens=max_tokens)
        elif "connection" in err.lower():
            log.warning("  Ollama server down — falling back to OpenRouter")
            return _openrouter_generate(prompt, system, tools=tools, max_tokens=max_tokens)

    return result


def is_ollama_available() -> bool:
    """Quick health check for Ollama."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
