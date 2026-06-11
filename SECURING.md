# SECURING.md

Security issues found during code review and the mitigations applied.

---

## 1. Timing Attack on Token Verification

**Severity:** High

**Problem:**
Token validation used a Python `set` lookup (`credentials.credentials not in VALID_TOKENS`). Set lookups are not constant-time: they short-circuit as soon as a match is found. In a high-precision environment, an attacker could measure response times to infer whether a partial token prefix exists, gradually narrowing down valid tokens.

**Fix:** `app/main.py`
```python
# Before
if credentials.credentials not in VALID_TOKENS:

# After
if not any(secrets.compare_digest(token, valid) for valid in VALID_TOKENS):
```
`secrets.compare_digest` compares two strings in constant time regardless of content, making timing measurements useless.

---

## 2. Information Disclosure via Error Messages

**Severity:** Medium

**Problem:**
The 400 error handler returned raw exception strings to the client (`detail=str(e)`). If a third-party library (pandas, ta) raised a `ValueError` with internal details — stack traces, column names, file paths — those would be exposed to the caller.

**Fix:** `app/main.py`

A whitelist of known safe messages was introduced. Only controlled strings produced by our own code pass through to the client; any other `ValueError` returns the generic message `"Invalid input data"`.

```python
safe_messages = {
    "Insufficient data for indicators after calculation",
    "Empty image bytes generated",
    "Data list is empty",
}
detail = msg if msg in safe_messages else "Invalid input data"
```

---

## 3. API Schema Publicly Exposed

**Severity:** Medium

**Problem:**
FastAPI enables `/docs` (Swagger UI), `/redoc`, and `/openapi.json` by default. Anyone could read the full API schema — endpoint structure, field names, validation rules, types — without authenticating. This gives attackers a complete map for crafting targeted payloads.

**Fix:** `app/main.py`
```python
app = FastAPI(
    title="Candle Graph API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
```

---

## 4. Semaphore Slot Starvation via Missing Timeout

**Severity:** High

**Problem:**
The semaphore limits concurrent chart generation to 4 slots. However, `asyncio.to_thread()` had no timeout: a crafted request (e.g. 1000 data points) could hold a semaphore slot indefinitely. With 4 simultaneous such requests all slots would be permanently occupied, making the API unresponsive to any subsequent caller.

**Fix:** `app/main.py`

`asyncio.wait_for` wraps both CPU-bound calls with a 30-second deadline. A timeout releases the semaphore slot immediately and returns `503` to the caller.

```python
CHART_TIMEOUT = 30  # seconds

df_with_indicators = await asyncio.wait_for(
    asyncio.to_thread(add_indicators, df, bb_k=body.bb_k),
    timeout=CHART_TIMEOUT,
)

img_bytes = await asyncio.wait_for(
    asyncio.to_thread(get_plot_bytes, df_with_indicators, body.symbol),
    timeout=CHART_TIMEOUT,
)
```

```python
except asyncio.TimeoutError:
    raise HTTPException(status_code=503, detail="Request timed out")
```

---

## 5. Infinite and NaN Float Values Accepted

**Severity:** Medium

**Problem:**
Pydantic accepts IEEE 754 special values (`Infinity`, `-Infinity`, `NaN`) for `float` fields. Injecting these into OHLCV data causes cascading NaN/Inf values during pandas rolling calculations and Matplotlib rendering, potentially triggering undefined behavior or crashing the rendering thread.

**Fix:** `app/main.py`

A field validator rejects non-finite values at the Pydantic layer, before any data reaches the business logic.

```python
@field_validator("open", "high", "low", "close", "volume")
@classmethod
def must_be_finite(cls, v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("Value must be finite")
    return v
```

---

## 6. Unbounded `bb_k` Parameter

**Severity:** Low

**Problem:**
The Bollinger Bands multiplier `bb_k` was only validated as `> 0`. An attacker could send `bb_k=1e300`, causing arithmetic overflow in the standard deviation multiplication and producing `Inf` values in the Bollinger Band columns.

**Fix:** `app/main.py`
```python
# Before
bb_k: float = Field(2.0, gt=0)

# After
bb_k: float = Field(2.0, gt=0, le=10)
```

---

## 7. Operational Information in `/health` Response

**Severity:** Low

**Problem:**
The public health endpoint returned `auth_enabled` and `concurrency_limit`. These values tell an attacker whether brute-forcing tokens is necessary and exactly how many concurrent requests are needed to saturate the semaphore.

**Fix:** `app/main.py`
```python
# Before
return {"status": "ok", "auth_enabled": True, "concurrency_limit": 4}

# After
return {"status": "ok"}
```

---

## 8. No Rate Limiting

**Severity:** Medium

**Problem:**
A caller with a valid token could send an unlimited burst of requests. Even with the semaphore in place, requests queue up as async coroutines — each consuming memory — before they are ever processed. This allows resource exhaustion without triggering any existing guard.

**Fix:** `app/main.py`, `requirements.txt`

`slowapi` was added as a dependency. The rate limit is configurable via the `RATE_LIMIT` environment variable (default: `20/minute`).

IP extraction is proxy-aware and tests headers in priority order to correctly identify the real client behind Cloudflare, nginx, or Apache:

```python
def get_client_ip(request: Request) -> str:
    cf_ip = request.headers.get("CF-Connecting-IP")   # Cloudflare
    if cf_ip:
        return cf_ip
    real_ip = request.headers.get("X-Real-IP")        # nginx
    if real_ip:
        return real_ip
    forwarded_for = request.headers.get("X-Forwarded-For")  # Apache / nginx
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
```

Requests exceeding the limit receive `429 Too Many Requests` before touching the semaphore or any business logic.

`.env` / `.env.example`:
```
RATE_LIMIT=20/minute   # supports: second, minute, hour, day
```
