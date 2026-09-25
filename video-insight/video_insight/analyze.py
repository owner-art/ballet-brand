"""영상 한 편 → 렌즈를 통과한 분석 결과."""

from __future__ import annotations

import base64
from typing import Any

from .claude import ask
from .lens import Lens
from .media import Frame
from .schemas import VideoAnalysis

SYSTEM = """당신은 영상 리서처다. 영상 한 편의 대본, 키프레임, 메타데이터를 받아
특정 프로젝트 관점(렌즈)에서 쓸모 있는 것만 정확히 뽑아낸다.

원칙:
- 본 것과 추정한 것을 구분한다. 화면·대본에 근거가 있으면 타임스탬프를 붙인다.
- 영상이 주장한 수치는 영상의 주장으로 적고, 사실로 단정하지 않는다.
- 렌즈 질문에 영상이 답하지 못하면 "영상에서 확인 불가"라고 쓰고 confidence는 low.
- implications는 프로젝트 문서·전략에 실제로 반영할 수 있는 구체적 행동으로 쓴다.
  "참고하면 좋다" 같은 말은 쓰지 않는다. 반영할 것이 없으면 비워 둔다.
- 결과는 {language}로 쓴다."""


def _lens_block(lens: Lens) -> str:
    qs = "\n".join(f"{i}. {q}" for i, q in enumerate(lens.questions, 1)) or "(없음)"
    return f"## 프로젝트: {lens.name}\n{lens.description}\n\n## 렌즈 질문\n{qs}"


def analyze(
    *,
    lens: Lens,
    url: str,
    metadata: str,
    transcript: str | None,
    frames: list[Frame],
    model: str,
) -> VideoAnalysis:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": _lens_block(lens)},
        {"type": "text", "text": f"## 영상\nURL: {url}\n{metadata}"},
        {
            "type": "text",
            "text": "## 대본\n" + (transcript or "(대본 없음 — 화면과 메타데이터만으로 분석)"),
        },
        {"type": "text", "text": f"## 키프레임 {len(frames)}장 (시간순)"},
    ]
    for f in frames:
        content.append({"type": "text", "text": f"[{f.stamp}]"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(f.path.read_bytes()).decode(),
            },
        })
    content.append({"type": "text", "text": "위 영상을 렌즈에 따라 분석하라."})
    return ask(
        system=SYSTEM.format(language="한국어" if lens.language == "ko" else lens.language),
        content=content,
        schema=VideoAnalysis,
        model=model,
    )
