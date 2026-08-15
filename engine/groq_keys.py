# groq_keys.py
# Shared multi-key rotation for every Groq call site in the app.
#
# groqkey.txt historically held exactly one key. As of 2026-07-29 it can hold
# several, semicolon-separated ("gsk_AAA;gsk_BBB;gsk_CCC") — added so one
# account hitting its rate/quota limit doesn't stall every LLM call (RAG
# answer generation in rag_engine.py, LLM-judge scoring in evaluate_engine.py)
# for the rest of the run. Both call sites share this module's rotation
# pointer so a key already marked limited by one isn't immediately retried by
# the other.
import os
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_PATH = os.path.join(BASE_DIR, "groqkey.txt")

_lock = threading.Lock()
_idx = 0


def _load() -> list:
    if not os.path.exists(KEY_PATH):
        return []
    with open(KEY_PATH, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    return [k.strip() for k in raw.split(";") if k.strip()]


_keys = _load()


def get_keys() -> list:
    """All configured keys, in rotation order. Re-reads groqkey.txt each call
    so editing the file takes effect on the next request without a restart."""
    global _keys
    fresh = _load()
    if fresh:
        _keys = fresh
    return list(_keys)


def current_key() -> str:
    keys = get_keys()
    if not keys:
        raise RuntimeError("groqkey.txt has no usable API key")
    with _lock:
        return keys[_idx % len(keys)]


def rotate_key() -> str:
    """Advance to the next key (wrapping around) and return it."""
    global _idx
    keys = get_keys()
    if not keys:
        raise RuntimeError("groqkey.txt has no usable API key")
    with _lock:
        _idx = (_idx + 1) % len(keys)
        return keys[_idx]


_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "429", "quota", "too many requests")


def is_rate_limit_error(e: Exception) -> bool:
    """True for a Groq rate-limit/quota error — checked both by type (the
    groq client raises RateLimitError, a distinct exception a caller should
    never accidentally string-match past) and by message text (langchain_groq
    sometimes wraps it, losing the original exception type)."""
    try:
        from groq import RateLimitError
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass
    return any(m in str(e).lower() for m in _RATE_LIMIT_MARKERS)


def reasoning_model_kwargs(model: str) -> dict:
    """Extra ChatGroq kwargs needed for openai/gpt-oss-* models specifically.

    These are reasoning models: Groq's default 2048-token completion cap is
    shared between hidden reasoning tokens and the actual visible answer, and
    at reasoning_effort="medium" (the API default) the model can burn the
    *entire* cap on reasoning before emitting any visible text —
    finish_reason="length", content="". Reproduced directly 2026-08-15
    migrating LLM_MODEL/JUDGE_MODEL to gpt-oss (see rag_engine.py/
    evaluate_engine.py): 2/3 and 1/3 repeated runs on the same prompt hit
    this, and the resulting blank answer sails straight through
    validate_answer_citations() (nothing in an empty string to reject) and
    ships with only the citation footer attached — a silent, wrong-looking-
    fine failure, not an exception anything catches.
    max_tokens=4096 gives reasoning + a real answer room to both fit;
    reasoning_effort="low" independently cuts how much of that budget
    reasoning actually uses (confirmed on repeat-testing: reasoning_tokens
    dropped from ~2046 to single/low-double digits, 8/8 clean runs). Not
    applicable to non-gpt-oss models (e.g. llama-3.x) — passing
    reasoning_effort to those errors."""
    if model.startswith("openai/gpt-oss"):
        return {"max_tokens": 4096, "model_kwargs": {"reasoning_effort": "low"}}
    return {}
