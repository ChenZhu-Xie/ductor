"""NDJSON parser for the Google Gemini CLI.

Translates Gemini-specific events into normalized StreamEvents.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ductor_bot.cli.stream_events import (
    AssistantTextDelta,
    ResultEvent,
    StreamEvent,
    SystemInitEvent,
    ToolResultEvent,
    ToolUseEvent,
)

logger = logging.getLogger(__name__)

_StreamParser = Callable[[dict[str, Any]], list[StreamEvent]]

# Matches patterns like "12.3% context used" or "(80% context used)"
CONTEXT_USAGE_RE = re.compile(r"(\d+(?:\.\d+)?)%\s+context\s+used", re.IGNORECASE)

# Known Gemini model context window limits (tokens)
# These act as the final fallback for percentage calculation.
GEMINI_MODEL_LIMITS: dict[str, int] = {
    "gemini-2.0-flash": 1_048_576,
    "gemini-2.0-pro-exp": 2_097_152,
    "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
    "gemini-1.0-pro": 32_768,
}


def parse_gemini_stream_line(line: str) -> list[StreamEvent]:
    """Parse a single NDJSON line from Gemini CLI into normalized stream events."""
    stripped = line.strip()
    if not stripped:
        return []

    try:
        data: dict[str, Any] = json.loads(stripped)
    except json.JSONDecodeError:
        logger.debug("Gemini: unparseable stream line: %.200s", stripped)
        return []

    parser = _STREAM_PARSERS.get(data.get("type", ""))
    return parser(data) if parser else []


def parse_gemini_json(raw: str) -> str:
    """Extract result text from Gemini CLI JSON batch output (non-streaming).

    Handles both dict (single result) and list (array of events) formats.
    """
    if not raw:
        return ""
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:2000]

    if isinstance(parsed, dict):
        return extract_result_text(parsed)

    if isinstance(parsed, list):
        texts = [extract_result_text(item) for item in parsed if isinstance(item, dict)]
        return "\n\n".join(text for text in texts if text)

    return ""


def _parse_gemini_message(data: dict[str, Any]) -> list[StreamEvent]:
    """Parse Gemini's flat message structure."""
    role = data.get("role")
    content = data.get("content")
    if role not in ("assistant", "model") or not content:
        return []

    if isinstance(content, str):
        # 优先从文本匹配百分比
        match = CONTEXT_USAGE_RE.search(content)
        usage_perc = float(match.group(1)) if match else None
        return [AssistantTextDelta(type="assistant", text=content, usage_perc=usage_perc)]

    if isinstance(content, list):
        events: list[StreamEvent] = []
        for block in content:
            events.extend(_parse_message_content_block(block))
        return events

    return []


def _parse_gemini_result(data: dict[str, Any]) -> ResultEvent:
    """Extract metrics and final output from Gemini's result event."""
    stats = data.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}

    usage: dict[str, Any] = {
        "input_tokens": stats.get("input_tokens", 0),
        "output_tokens": stats.get("output_tokens", 0),
        "cached_tokens": stats.get("cached_tokens", stats.get("cached", 0)),
    }

    # Handle nested model stats from newer Gemini CLI versions
    model_stats = stats.get("models", {})
    if isinstance(model_stats, dict) and model_stats:
        max_input = -1
        for m_name, m_data in model_stats.items():
            if not isinstance(m_data, dict):
                continue
            m_tokens = m_data.get("tokens", {})
            if not isinstance(m_tokens, dict):
                continue
            m_input = m_tokens.get("input", 0)
            if m_input > max_input:
                max_input = m_input
                usage["input_tokens"] = m_input
                usage["output_tokens"] = m_tokens.get("candidates", 0)
                usage["cached_tokens"] = m_tokens.get("cached", 0)
                usage["active_model"] = m_name

    # 结果文本提取
    res = extract_result_text(data)

    # 优先匹配逻辑：文本正则 > stats 显式字段 > token 计算 fallback
    usage_perc: float | None = None
    
    # 1. 尝试从结果文本匹配
    if res:
        match = CONTEXT_USAGE_RE.search(res)
        if match:
            usage_perc = float(match.group(1))

    # 2. 回退到 stats 显式比例
    if usage_perc is None:
        if "context_usage_ratio" in stats:
            usage_perc = float(stats["context_usage_ratio"]) * 100
        elif "usage_perc" in stats:
            usage_perc = float(stats["usage_perc"])

    # 3. 终极回退：通过 token 计算
    if usage_perc is None and usage["input_tokens"] > 0:
        model_name = usage.get("active_model", "")
        # Try to find a limit for the active model
        limit = GEMINI_MODEL_LIMITS.get(model_name)
        if not limit:
            # Fallback to a sensible default if model is unknown (e.g. 1M for flash)
            limit = 1_048_576
        
        total_tokens = usage["input_tokens"] + usage["output_tokens"]
        usage_perc = (total_tokens / limit) * 100

    is_error = bool(data.get("is_error")) or data.get("status") == "error"

    if not res and is_error:
        err = data.get("error")
        if isinstance(err, dict):
            res = extract_text(err, ("message", "error", "detail"))
        elif err is not None:
            res = str(err)

    return ResultEvent(
        type="result",
        session_id=data.get("session_id"),
        result=res or "",
        is_error=is_error,
        duration_ms=stats.get("duration_ms"),
        usage=usage,
        usage_perc=usage_perc,
    )


def _parse_gemini_init(data: dict[str, Any]) -> list[StreamEvent]:
    return [
        SystemInitEvent(
            type="system",
            subtype="init",
            session_id=data.get("session_id"),
        ),
    ]


def _parse_gemini_tool_use(data: dict[str, Any]) -> list[StreamEvent]:
    return [
        ToolUseEvent(
            type="assistant",
            tool_name=str(data.get("tool_name") or data.get("name") or ""),
            tool_id=_as_optional_str(data.get("tool_id") or data.get("id")),
            parameters=_as_dict(data.get("parameters") or data.get("input")),
        ),
    ]


def _parse_gemini_tool_result(data: dict[str, Any]) -> list[StreamEvent]:
    return [
        ToolResultEvent(
            type="tool_result",
            tool_id=str(data.get("tool_id", "")),
            status=str(data.get("status", "")),
            output=str(data.get("output", "")),
        ),
    ]


def _parse_gemini_result_event(data: dict[str, Any]) -> list[StreamEvent]:
    return [_parse_gemini_result(data)]


def _parse_gemini_error(data: dict[str, Any]) -> list[StreamEvent]:
    return [
        ResultEvent(
            type="result",
            result=extract_text(data, ("message", "error", "detail")) or "Unknown Gemini error",
            is_error=True,
        ),
    ]


def _parse_message_content_block(block: Any) -> list[StreamEvent]:
    if not isinstance(block, dict):
        return []

    block_type = block.get("type")
    if block_type == "text":
        return [AssistantTextDelta(type="assistant", text=str(block.get("text", "")))]
    if block_type == "tool_use":
        return [
            ToolUseEvent(
                type="assistant",
                tool_name=str(block.get("name", "")),
                tool_id=_as_optional_str(block.get("id")),
                parameters=_as_dict(block.get("input")),
            ),
        ]
    return []


def extract_result_text(data: dict[str, Any]) -> str:
    return extract_text(data, ("result", "response", "content", "output"))


def extract_text(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        return value if isinstance(value, str) else str(value)
    return ""


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


_STREAM_PARSERS: dict[str, _StreamParser] = {
    "init": _parse_gemini_init,
    "message": _parse_gemini_message,
    "tool_use": _parse_gemini_tool_use,
    "tool_result": _parse_gemini_tool_result,
    "result": _parse_gemini_result_event,
    "error": _parse_gemini_error,
}
