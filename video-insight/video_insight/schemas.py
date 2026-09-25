"""Claude가 돌려주는 구조화된 결과의 스키마."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Level = Literal["low", "medium", "high"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Moment(_Strict):
    timestamp: str = Field(description="mm:ss")
    what: str


class LensFinding(_Strict):
    question: str = Field(description="렌즈 질문 원문")
    answer: str
    evidence: str = Field(description="영상 속 근거 — 타임스탬프, 화면, 발언 인용")
    confidence: Level


class Implication(_Strict):
    target: str = Field(description="프로젝트의 어느 부분에 해당하는지 (문서 섹션, 제품, 채널 등)")
    action: str = Field(description="구체적으로 무엇을 바꾸거나 더할지")
    rationale: str
    priority: Level


class VideoAnalysis(_Strict):
    title: str = Field(description="분석 노트 제목 (한 줄)")
    summary: str = Field(description="영상이 무엇이고 무엇을 말하는지 3~5문장")
    format: str = Field(description="영상 형식: 튜토리얼, 브이로그, 광고, 리뷰, 밈/챌린지 등")
    hook: str = Field(description="첫 3초가 시선을 잡는 방식")
    moments: list[Moment]
    visual_observations: list[str] = Field(description="의상, 색, 소재, 연출, 구도 등 화면에서 본 것")
    audience_signals: list[str] = Field(description="조회·반응 수치, 댓글의 결, 타깃 추정")
    claims_and_data: list[str] = Field(description="영상이 주장하거나 제시한 사실·수치 (검증 필요 여부 표시)")
    lens_findings: list[LensFinding]
    implications: list[Implication]
    tags: list[str]


class Edit(_Strict):
    file: str = Field(description="context_files 중 하나의 상대 경로")
    old_string: str = Field(description="파일에 정확히 한 번 등장하는 원문 조각 (공백·태그 포함 그대로)")
    new_string: str
    reason: str
    sources: list[str] = Field(description="근거가 된 분석 노트 id 목록")


class Proposal(_Strict):
    summary: str = Field(description="이번 반영안의 요지 2~4문장")
    edits: list[Edit]
    not_applied: list[str] = Field(description="중요하지만 문서 수정으로 옮기지 않은 인사이트와 그 이유")
    open_questions: list[str]


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 스키마를 structured outputs용으로 평탄화한다 ($ref 인라인, title 제거)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            out = {}
            for k, v in node.items():
                if k == "properties":  # 키는 필드 이름이므로 그대로 둔다 ("title" 필드 포함)
                    out[k] = {name: walk(sub) for name, sub in v.items()}
                elif k not in ("title", "default"):
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
