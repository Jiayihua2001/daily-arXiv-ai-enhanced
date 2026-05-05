"""Compatibility shim for OpenAI's chat.completions.create across model
families. Both screen.py and enhance.py route their LLM calls through
this so they handle parameter-name churn without per-call try/except.

Currently handles:
  - max_tokens vs max_completion_tokens (gpt-5 family + o-series renamed it)
  - unsupported temperature on o-series (must be omitted / default to 1.0)
  - unsupported response_format on some models (silently dropped on 400)
"""
from __future__ import annotations


_MODERN_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def chat_create(client, **kwargs):
    """Wrapper around client.chat.completions.create() that auto-translates
    parameter names for newer OpenAI model families and quietly drops
    unsupported parameters when the API rejects them.
    """
    model = kwargs.get("model", "")
    is_modern = any(model.startswith(p) for p in _MODERN_PREFIXES)

    # Happy path: use the new param name on modern models from the FIRST
    # try so success is one network call. Older models start with max_tokens.
    if is_modern and "max_tokens" in kwargs and "max_completion_tokens" not in kwargs:
        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")

    # Up to 4 retries to cover sequential parameter migrations.
    for _ in range(4):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            msg = str(e).lower()

            # max_tokens → max_completion_tokens
            if ("max_completion_tokens" in msg
                    and "max_tokens" in kwargs
                    and "max_completion_tokens" not in kwargs):
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                continue

            # max_completion_tokens → max_tokens (rare; older model with new name)
            if ("max_tokens" in msg and "instead" in msg
                    and "max_completion_tokens" in kwargs
                    and "max_tokens" not in kwargs):
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                continue

            # temperature unsupported (o-series only allows default)
            if ("temperature" in msg and "unsupported" in msg
                    and "temperature" in kwargs):
                kwargs.pop("temperature", None)
                continue

            # response_format unsupported on some models
            if ("response_format" in msg
                    and ("unsupported" in msg or "not supported" in msg)
                    and "response_format" in kwargs):
                kwargs.pop("response_format", None)
                continue

            # "model output limit reached" — reasoning models (gpt-5/o-series)
            # consume internal reasoning_tokens before output. If our budget
            # was tiny, bump it 4x and retry, capping at 4096 to avoid runaway.
            if (("model output limit" in msg)
                    or ("max_tokens" in msg and "reached" in msg)
                    or ("max_completion_tokens" in msg and "reached" in msg)):
                key = "max_completion_tokens" if "max_completion_tokens" in kwargs else "max_tokens"
                current = kwargs.get(key, 100)
                new_val = min(max(current * 4, 256), 4096)
                if new_val > current:
                    kwargs[key] = new_val
                    continue

            # Not a known parameter migration — propagate the error
            raise

    # If we exhausted retries, one last attempt with current kwargs
    return client.chat.completions.create(**kwargs)
