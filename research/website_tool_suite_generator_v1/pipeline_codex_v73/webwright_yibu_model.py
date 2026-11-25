"""Bounded, spend-logged Yibu backend for the actual WebWright runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from cuawright.webwright.models.openrouter_model import (
    OpenRouterModel,
    OpenRouterModelConfig,
)
from pydantic import field_validator

from api_propose import _failed_usage, _logger, _model_used, _resolve_api_key, _safe_detail_log
from local_propose import _append_jsonl


DEFAULT_LEDGER = Path("/shared/hans/webarena/spend_ledger.jsonl")


def _model_event(event: str, **metadata: Any) -> None:
    """Optional metadata-only host timeline; prompts and credentials stay out."""
    path = os.environ.get('CODEX_MODEL_TIMELINE_PATH')
    if not path:
        return
    try:
        from event_log import emit
        emit(Path(path), 'webwright_model', event, **metadata)
    except (OSError, ImportError, ValueError, TypeError) as error:
        print('model timeline unavailable: ' + type(error).__name__, file=sys.stderr, flush=True)


def ledger_context(log_path: Path) -> dict[str, str]:
    """Make concurrently interleaved calls attributable to their run/stage."""
    parts = log_path.parts
    stage = ('visible_reference_advisory' if 'visible_reference_advisory' in parts
             else 'independent_reference' if any(part in {'independent_reference', 'independent_reference_fallback'}
                                                 for part in parts)
             else 'tool_generation')
    return {'experiment_artifact_path': str(log_path), 'experiment_stage': stage}


class YibuWebWrightModelConfig(OpenRouterModelConfig):
    api_key_file: Path
    api_log_path: Path
    spend_ledger_path: Path = DEFAULT_LEDGER
    max_api_calls: int = 8

    @field_validator("max_api_calls")
    @classmethod
    def validate_max_api_calls(cls, value: int) -> int:
        if not 1 <= value <= 110:
            raise ValueError("max_api_calls must be between 1 and 110")
        return value


class YibuWebWrightModel(OpenRouterModel):
    """OpenAI-compatible chat backend with no retries and mandatory logging."""

    _DEFAULT_CONFIG_CLASS = YibuWebWrightModelConfig
    _MAX_RATE_LIMIT_RETRIES = 0
    _MAX_TRANSIENT_RETRIES = 0

    def __init__(self, **kwargs: Any):
        key_file = Path(kwargs["api_key_file"])
        kwargs["openrouter_api_key"] = _resolve_api_key(key_file)
        super().__init__(config_class=YibuWebWrightModelConfig, **kwargs)
        self._api_call_count = 0
        self._api_logger = _logger(self.config.api_log_path)

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._api_call_count >= self.config.max_api_calls:
            raise RuntimeError(
                f"WebWright API-call cap reached ({self.config.max_api_calls})"
            )
        self._api_call_count += 1
        started = time.monotonic()
        operation_id = uuid4().hex
        _model_event('request_started', operation_id=operation_id, call_index=self._api_call_count,
                     requested_model=self.config.model_name,
                     provider=urlparse(self.config.openrouter_endpoint).netloc)
        response: dict[str, Any] = {}
        try:
            response = await super()._post_with_retries(payload)
        except BaseException as error:
            duration_ms = (time.monotonic() - started) * 1000
            _model_event('request_finished', operation_id=operation_id, call_index=self._api_call_count,
                         status='failed' if isinstance(error, Exception) else 'interrupted',
                         error_type=type(error).__name__, duration_ms=round(duration_ms, 3),
                         usage='unreported')
            detail_log_error = _safe_detail_log(self._api_logger,
                model=self.config.model_name,
                request_data=payload,
                response_data={"usage": {}},
                user="hans_website_tool_webwright",
                duration_ms=duration_ms,
                success=False,
                error=f"{type(error).__name__}: {str(error)[:1000]}",
            )
            _append_jsonl(self.config.spend_ledger_path, {
                **ledger_context(self.config.api_log_path),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": self.config.model_name,
                "requested_model": self.config.model_name,
                "provider": urlparse(self.config.openrouter_endpoint).netloc,
                "in": None,
                "cached_in": None,
                "out": None,
                "reasoning_out": None,
                "total_tokens": None,
                "usd": None,
                "cost_source": "monthly_quota_rate_not_exposed",
                "billing_mode": "monthly_quota",
                "purpose": "website_tool_webwright_repair",
                "status": "api_failure" if isinstance(error, Exception) else "interrupted",
                "usage_source": "unreported_after_failure" if isinstance(error, Exception) else "unreported_after_interruption",
                "error_type": type(error).__name__,
                "detail_log_error_type": detail_log_error,
                "call_index": self._api_call_count,
            })
            raise

        duration_ms = (time.monotonic() - started) * 1000
        usage = _failed_usage(response)
        model_used = _model_used(response, self.config.model_name)
        _model_event('request_finished', operation_id=operation_id, call_index=self._api_call_count,
                     status='completed', requested_model=self.config.model_name, returned_model=model_used,
                     duration_ms=round(duration_ms, 3), input_tokens=usage['input_tokens'],
                     output_tokens=usage['output_tokens'], cached_input_tokens=usage['cached_input_tokens'],
                     reasoning_output_tokens=usage['reasoning_output_tokens'], total_tokens=usage['total_tokens'])
        detail_log_error = _safe_detail_log(self._api_logger,
            model=model_used,
            request_data=payload,
            response_data=response,
            user="hans_website_tool_webwright",
            duration_ms=duration_ms,
            success=True,
        )
        _append_jsonl(self.config.spend_ledger_path, {
            **ledger_context(self.config.api_log_path),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model_used,
            "requested_model": self.config.model_name,
            "provider": urlparse(self.config.openrouter_endpoint).netloc,
            "in": usage["input_tokens"],
            "cached_in": usage["cached_input_tokens"],
            "out": usage["output_tokens"],
            "reasoning_out": usage["reasoning_output_tokens"],
            "total_tokens": usage["total_tokens"],
            "usd": None,
            "cost_source": "monthly_quota_rate_not_exposed",
            "billing_mode": "monthly_quota",
            "purpose": "website_tool_webwright_repair",
            "status": "completed",
            "detail_log_error_type": detail_log_error,
            "usage_source": ("provider_response" if usage["input_tokens"] is not None
                             and usage["output_tokens"] is not None else "incomplete_or_unreported_after_success"),
            "call_index": self._api_call_count,
        })
        return response

