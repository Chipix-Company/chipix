"""Sentry initialization for FastAPI backend."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

_initialized = False

# Public client DSN (safe to embed — it can only send events, not read them).
# Override at runtime with CHIPVERIFY_SENTRY_DSN.
DEFAULT_SENTRY_DSN = (
    "https://f70efb11415defc8fe101378ef8a97c7@o4511364292673536.ingest.us.sentry.io/4511364402839552"
)


def _resolve_dsn() -> str:
    return os.environ.get("CHIPVERIFY_SENTRY_DSN", "").strip() or DEFAULT_SENTRY_DSN


def init_sentry() -> bool:
    """Initialize Sentry once at process startup. No-op when DSN is unset."""
    global _initialized
    if _initialized:
        return True

    dsn = _resolve_dsn()
    if not dsn:
        return False

    try:
        import logging as _logging

        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry-sdk not installed; error tracking disabled")
        return False

    environment = os.environ.get("CHIPVERIFY_SENTRY_ENVIRONMENT", "development").strip()
    traces_sample_rate = float(os.environ.get("CHIPVERIFY_SENTRY_TRACES_SAMPLE_RATE", "0.2"))
    release = os.environ.get("CHIPVERIFY_SENTRY_RELEASE", "").strip() or None

    # Capture every error the backend logs (logger.error / logger.exception) as a Sentry
    # event, and keep INFO+ log lines as breadcrumbs for context. This is what makes
    # "all the errors" show up, not just unhandled HTTP exceptions.
    logging_integration = LoggingIntegration(
        level=_logging.INFO,
        event_level=_logging.ERROR,
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            logging_integration,
        ],
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        attach_stacktrace=True,
        # Group local/dev events separately and tag the process so backend vs electron
        # vs renderer events are easy to filter in the Sentry UI.
        before_send=_scrub_sensitive_event,
    )
    sentry_sdk.set_tag("runtime", "python-backend")
    sentry_sdk.set_tag("frozen", str(bool(getattr(sys, "frozen", False))))
    _initialized = True
    logger.info("Sentry initialized (environment=%s)", environment)
    return True


def _scrub_sensitive_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            if key.lower() in ("authorization", "cookie", "x-api-key"):
                headers[key] = "[Filtered]"
    return event


def capture_exception(error: BaseException, **context: Any) -> None:
    """Report an exception to Sentry when configured."""
    if not _resolve_dsn():
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    if not _initialized:
        init_sentry()
    with sentry_sdk.push_scope() as scope:
        for key, value in context.items():
            if value is not None:
                scope.set_extra(key, value)
        sentry_sdk.capture_exception(error)
