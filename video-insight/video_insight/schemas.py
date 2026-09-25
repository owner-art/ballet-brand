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


# ── research: 주제 → 검색 → 선별 → 보고서 ─────────────────────────────


class SearchPlan(_Strict):
    youtube_queries: list[str] = Field(description="유튜브 검색어 (한국어 위주, 필요하면 영어)")
    social_queries: list[str] = Field(description="인스타그램 릴스·틱톡을 찾기 위한 웹 검색어")
    rationale: str


class Pick(_Strict):
    index: int = Field(description="후보 목록의 번호")
    reason: str


class Screening(_Strict):
    picks: list[Pick]
    note: str = Field(description="선별 기준과 제외한 후보의 경향 한두 문장")


class Evidence(_Strict):
    note_id: str
    timestamp: str = Field(description="mm:ss, 모르면 빈 문자열")
    quote: str = Field(description="대본 인용 또는 화면 묘사")


class Finding(_Strict):
    finding: str
    evidence: list[Evidence]
    strength: Level = Field(description="몇 편이 얼마나 분명하게 뒷받침하는지")


class ResearchReport(_Strict):
    title: str
    answer: str = Field(description="리서치 질문에 대한 핵심 답 3~5문장")
    findings: list[Finding]
    content_patterns: list[str] = Field(description="잘 되는 영상들의 형식·훅·연출 공통점")
    disagreements: list[str] = Field(description="영상끼리 엇갈리거나 프로젝트 가설과 부딪히는 지점")
    gaps: list[str] = Field(description="영상으로는 확인하지 못한 것, 추가로 필요한 근거")
    implications: list[Implication]
    next_queries: list[str] = Field(description="다음에 파볼 검색어")
