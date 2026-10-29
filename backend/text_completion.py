"""Shared hosted/local JSON completion path with automatic local Qwen fallback."""
import json
import re

import requests

from local_qwen import chat_completion as local_chat_completion

PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
}
PROVIDER_NAMES = {"openrouter": "OpenRouter", "groq": "Groq (GroqCloud)", "local": "Local Qwen3 1.7B Q4_K_M"}


def _validate_json_object(content: str, validator=None) -> dict:
    raw = re.sub(r"^\s*```(?:json)?|```\s*$", "", str(content or "").strip(), flags=re.I)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model did not return a JSON object.")
    value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("The model returned an invalid JSON object.")
    if validator:
        validator(value)
    return value


def _remote_completion(provider, model, api_key, system_prompt, user_prompt, temperature, max_tokens, task, validator=None):
    if not api_key:
        raise RuntimeError(f"{PROVIDER_NAMES[provider]} API key is not configured.")
    if not model:
        raise RuntimeError(f"Choose a {PROVIDER_NAMES[provider]} text model in Settings.")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers["X-Title"] = "CreatorFlow"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if provider == "groq" and "gpt-oss" in model.lower():
        body["reasoning_effort"] = "low"
    try:
        response = requests.post(PROVIDER_URLS[provider], headers=headers, json=body, timeout=180)
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach {PROVIDER_NAMES[provider]} for {task}: {type(exc).__name__}: {exc}") from exc
    if not response.ok:
        try:
            payload = response.json()
            detail = (payload.get("error") or {}).get("message") if isinstance(payload, dict) else None
        except (ValueError, TypeError):
            detail = None
        detail = str(detail or response.text or "").strip()[:500]
        raise RuntimeError(f"{PROVIDER_NAMES[provider]} returned HTTP {response.status_code} for {task}: {detail or 'request failed'}")
    try:
        payload = response.json()
        choice = payload["choices"][0]
        if str(choice.get("finish_reason") or "").lower() == "length":
            raise RuntimeError(f"{PROVIDER_NAMES[provider]} stopped at the {max_tokens}-token output limit during {task}.")
        content = choice["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
        if not str(content or "").strip():
            raise ValueError("empty response")
        _validate_json_object(content, validator)
        return str(content)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise RuntimeError(f"{PROVIDER_NAMES[provider]} returned an unusable JSON response for {task}: {exc}") from exc


def _fallback_failure_summary(error, api_key="", limit=320):
    """Make the hosted-provider failure visible without ever echoing a saved key."""
    summary = " ".join(str(error or "Unknown provider failure").split())
    if api_key:
        summary = summary.replace(api_key, "[redacted]")
    if len(summary) > limit:
        summary = summary[:limit - 1].rstrip() + "…"
    return summary


def complete_json(provider, model, api_key, system_prompt, user_prompt, *, temperature, max_tokens, task, validator=None):
    """Return (JSON text, source label), falling back to local Qwen for failed hosted calls."""
    provider = str(provider or "openrouter").strip().lower()
    if provider == "local":
        content = local_chat_completion(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
        _validate_json_object(content, validator)
        return content, PROVIDER_NAMES["local"]
    if provider not in PROVIDER_URLS:
        provider = "openrouter"
    try:
        content = _remote_completion(provider, model, api_key, system_prompt, user_prompt, temperature, max_tokens, task, validator)
        return content, f"{PROVIDER_NAMES[provider]} · {model}"
    except Exception as primary_error:
        try:
            content = local_chat_completion(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
            _validate_json_object(content, validator)
            reason = _fallback_failure_summary(primary_error, api_key)
            return content, f"{PROVIDER_NAMES['local']} fallback after {PROVIDER_NAMES[provider]} failure: {reason}"
        except Exception as fallback_error:
            raise RuntimeError(
                f"{PROVIDER_NAMES[provider]} failed ({primary_error}); local Qwen fallback could not complete this step ({fallback_error}). "
                "Download the local model and ensure the llama.cpp runtime is installed, or check the selected provider."
            ) from primary_error
