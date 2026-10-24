"""LLM client for the agents. Gemini everywhere; Ollama only on a local run.

WIQAYA_ENV=local (default) | production. Set WIQAYA_ENV=production on every deployment: Ollama is then never used.
WIQAYA_LLM=gemini | ollama | off forces a provider (ollama only takes effect on a local run).
Every call can fail or time out: callers get None and use their rule-based fallback.
"""
import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT_S = float(os.getenv("WIQAYA_LLM_TIMEOUT", "40"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
try:  # python.org builds on macOS ship without system CA certificates; certifi supplies them
    import certifi
    _TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _TLS = ssl.create_default_context()


def _load_dotenv():
    """Read KEY=VALUE lines from wiqaya/.env without overriding the real environment."""
    path = Path(__file__).parent.parent / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_dotenv()


def is_local():
    return os.getenv("WIQAYA_ENV", "local") == "local"


def provider():
    """Gemini when a key is set; Ollama only on a local run (asked for, or no key); otherwise rules only."""
    p = os.getenv("WIQAYA_LLM")
    if p == "off":
        return "off"
    if p == "ollama" and is_local():
        return "ollama"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "ollama" if is_local() and p != "gemini" else "off"


def model():
    return (os.getenv("WIQAYA_GEMINI_MODEL", "gemini-3.1-flash-lite") if provider() == "gemini"
            else os.getenv("WIQAYA_MODEL", "gemma3:1b"))


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_TLS) as r:
        return json.loads(r.read())


def chat(system, user, schema=None, temperature=0.0):
    """Returns the model's text (a JSON string when `schema` is given), or None on any failure."""
    try:
        if provider() == "gemini":
            if schema:  # the schema rides in the prompt; agents validate every value against it anyway
                system = f"{system}\nReply with JSON only, matching this JSON schema: {json.dumps(schema)}"
            body = {"systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {"temperature": temperature,
                                         **({"responseMimeType": "application/json"} if schema else {})}}
            out = _post(f"{GEMINI_URL}/{model()}:generateContent", body, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            return "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"]).strip() or None
        if provider() == "ollama":
            body = {"model": model(), "stream": False, "options": {"temperature": temperature},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if schema:
                body["format"] = schema  # Ollama constrains decoding to this JSON schema
            return _post(f"{OLLAMA_URL}/api/chat", body, {})["message"]["content"].strip() or None
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, OSError):
        return None
    return None


def chat_json(system, user, schema):
    text = chat(system, user, schema)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def status():
    p = provider()
    if p == "off":
        return {"provider": "off", "model": None, "online": False}
    if p == "gemini":
        return {"provider": "gemini", "model": model(), "online": bool(os.getenv("GEMINI_API_KEY"))}
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
        return {"provider": "ollama", "model": model(), "online": model() in names}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {"provider": "ollama", "model": model(), "online": False}
