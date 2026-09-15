#!/usr/bin/env python3
"""Standalone Gemini AI API key tester.

Usage:
  python3 gemini_key_tester.py AIza...
  python3 gemini_key_tester.py                  # reads GEMINI_API_KEY / GOOGLE_API_KEY
  echo AIza... | python3 gemini_key_tester.py -
  python3 gemini_key_tester.py --prompt "Say hi" AIza...

Exit codes: 0 the key works, 1 the key is unusable, 2 the key itself is
live but Gemini is blocked for it (the API is disabled for its project,
or the key's restrictions block the service).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_PROMPT = "Reply with exactly: Gemini key works."
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)


def probe(key: str, prompt: str, model: str, timeout: float = 20.0) -> dict:
    url = ENDPOINT.format(model=model, key=key.strip())
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 64, "temperature": 0},
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "gemini-key-tester/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
            text = _extract_text(payload)
            return {
                "ok": True,
                "status": resp.status,
                "model": model,
                "text": text,
                "raw": payload,
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail_json = json.loads(detail)
        except json.JSONDecodeError:
            detail_json = detail
        result = {
            "ok": False,
            "status": exc.code,
            "model": model,
            "error": detail_json,
            "reason": _reason(exc.code, detail_json),
        }
        if result["reason"] in ("api_disabled_in_project", "key_restricted_from_service"):
            # the denial itself names the project or restricts an
            # authenticated key, proving the key is live
            haystack = " ".join((
                _error_message(detail_json),
                str(_errorinfo(detail_json).get("consumer") or ""),
            ))
            result["key_live"] = True
            m = _PROJECT_RE.search(haystack)
            if m:
                result["project"] = m.group(1)
            m = _ENABLE_URL_RE.search(haystack)
            if m:
                result["enable_url"] = m.group(0)
        return result
    except Exception as exc:  # noqa: BLE001 — surface any network/runtime failure
        return {"ok": False, "status": None, "model": model, "error": str(exc), "reason": "network"}


def _extract_text(payload: dict) -> str:
    chunks: list[str] = []
    for cand in payload.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


_PROJECT_RE = re.compile(r"projects?[/ ](\d+)")
_ENABLE_URL_RE = re.compile(r"https://console\.developers\.google\.com/\S+")

# ErrorInfo reasons that prove the key itself authenticated and resolved to a
# project; only its usage restrictions block this particular service
_LIVE_BLOCK_REASONS = {
    "API_KEY_SERVICE_BLOCKED",
    "API_TARGET_BLOCKED",
    "IP_ADDRESS_BLOCKED",
    "REFERER_BLOCKED",
}


def _error_message(detail) -> str:
    if isinstance(detail, dict):
        err = detail.get("error") or {}
        return str(err.get("message") or err.get("status") or "")
    if isinstance(detail, str):
        return detail
    return ""


def _errorinfo(detail) -> dict:
    """Normalized google.rpc.ErrorInfo: {"reason": ..., "consumer": "projects/NNN"}."""
    if not isinstance(detail, dict):
        return {}
    for item in (detail.get("error") or {}).get("details") or []:
        if isinstance(item, dict) and isinstance(item.get("reason"), str) and item.get("domain"):
            metadata = item.get("metadata") or {}
            return {"reason": item["reason"], "consumer": str(metadata.get("consumer") or "")}
    return {}


def _reason(status: int, detail) -> str:
    lower = _error_message(detail).lower()
    info = _errorinfo(detail)
    reason_code = info.get("reason", "").upper()
    if "has not been used in project" in lower or "enable it by visiting" in lower:
        # project-level denial: Google resolved the key to that project, the
        # Generative Language API is just not enabled on it
        return "api_disabled_in_project"
    if reason_code in _LIVE_BLOCK_REASONS:
        # the key authenticated; its restrictions block this service
        return "key_restricted_from_service"
    if status in (400, 401, 403):
        if reason_code in ("API_KEY_INVALID", "API_KEY_EXPIRED") or "api key not valid" in lower \
                or "invalid" in lower or "api key expired" in lower:
            return "invalid_key"
        if "permission" in lower or "blocked" in lower or "disabled" in lower:
            return "key_blocked_or_restricted"
        if status == 400:
            return "bad_request"
        return "unauthorized"
    if status == 404:
        return "model_not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "http_error"


def _resolve_key(args: argparse.Namespace) -> str:
    if args.key == "-":
        return sys.stdin.read().strip()
    if args.key:
        return args.key.strip()
    for env in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_AI_API_KEY"):
        val = os.environ.get(env, "").strip()
        if val:
            return val
    raise SystemExit(
        "No API key provided. Pass it as an arg, via stdin (-), "
        "or set GEMINI_API_KEY / GOOGLE_API_KEY."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Test a Google Gemini API key with a sample prompt.",
        epilog="exit codes: 0 key works, 1 key unusable, "
               "2 key live but Gemini is blocked (API disabled for its project, or key restrictions)",
    )
    parser.add_argument("key", nargs="?", help="API key, or '-' to read from stdin")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Sample prompt to send")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model id (default: {DEFAULT_MODEL})")
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    args = parser.parse_args(argv)

    key = _resolve_key(args)
    if not key.startswith("AIza") and not args.json:
        print(f"warning: key does not look like a Google API key (expected AIza…)", file=sys.stderr)

    result = probe(key, args.prompt, args.model, timeout=args.timeout)

    if args.json:
        # Never echo the key back.
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif result["ok"]:
        print(f"OK  status={result['status']} model={result['model']}")
        print(f"prompt: {args.prompt}")
        print(f"reply:  {result['text'] or '(empty)'}")
    elif result.get("key_live"):
        print(f"LIVE status={result['status']} reason={result['reason']} model={result['model']}")
        where = f"project {result['project']}" if result.get("project") else "a live project"
        if result["reason"] == "api_disabled_in_project":
            cause = "the Generative Language (Gemini) API is disabled there"
        else:
            cause = "the key's restrictions block the Generative Language (Gemini) API"
        print(f"note: the key itself is valid - Google resolved it to {where}, but {cause}")
        if result.get("enable_url"):
            print(f"enable: {result['enable_url']}")
    else:
        print(
            f"FAIL status={result.get('status')} reason={result.get('reason')} model={result['model']}",
            file=sys.stderr,
        )
        err = result.get("error")
        if isinstance(err, dict):
            msg = (err.get("error") or {}).get("message") or err
            print(f"error: {msg}", file=sys.stderr)
        else:
            print(f"error: {err}", file=sys.stderr)

    if result.get("ok"):
        return 0
    if result.get("key_live"):
        return 2  # live key, but Gemini is blocked for it
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
