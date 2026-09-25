"""Claude API 호출: 구조화된 JSON 결과를 받아 Pydantic 모델로 검증한다."""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from .schemas import strict_schema

DEFAULT_MODEL = os.environ.get("VI_MODEL", "claude-opus-5")
T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def ask(
    *,
    system: str,
    content: list[dict[str, Any]],
    schema: type[T],
    model: str = DEFAULT_MODEL,
    effort: str = "high",
) -> T:
    """한 번의 요청으로 schema 형태의 결과를 받는다.

    - 입력이 크므로(프레임 이미지 + 대본) 스트리밍으로 받아 타임아웃을 피한다.
    - 안전 분류기가 거절하면 서버가 다른 모델로 다시 돌리도록 fallbacks를 켠다.
    """
    with client().beta.messages.stream(
        model=model,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": strict_schema(schema)},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise RuntimeError(f"Claude가 이 요청을 거절했습니다: {msg.stop_details}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens에서 잘렸습니다. 프레임 수나 입력을 줄여 다시 시도하세요.")

    text = "".join(b.text for b in msg.content if b.type == "text")
    return schema.model_validate(json.loads(text))

