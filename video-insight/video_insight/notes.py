"""분석 노트를 프로젝트의 notes_dir에 쌓는다.

notes_dir/
  <key>.json      기계가 읽는 원본 (propose 단계 입력)
  <key>.md        사람이 읽는 노트
  <key>.txt       영상 전체 대본 (타임스탬프 포함)
  INDEX.md        전체 목록 (매번 다시 만든다)
  proposals/      반영안 기록
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .schemas import VideoAnalysis

_PRIO = {"high": "상", "medium": "중", "low": "하"}


def exists(notes: Path, key: str) -> bool:
    return (notes / f"{key}.json").exists()


def save(
    notes: Path,
    *,
    key: str,
    url: str,
    platform: str,
    info: dict[str, Any],
    analysis: VideoAnalysis,
    model: str,
    transcript_source: str,
    transcript: str | None = None,
) -> Path:
    notes.mkdir(parents=True, exist_ok=True)
    if transcript:
        (notes / f"{key}.txt").write_text(
            f"# {info.get('title') or key}\n# {url}\n# 대본 출처: {transcript_source}\n\n{transcript}\n",
            encoding="utf-8",
        )
    record = {
        "id": key,
        "url": url,
        "platform": platform,
        "analyzed_on": date.today().isoformat(),
        "model": model,
        "transcript_source": transcript_source,
        "transcript_file": f"{key}.txt" if transcript else None,
        "proposed_in": None,
        "meta": {
            k: info.get(k)
            for k in ("title", "uploader", "upload_date", "duration", "view_count", "like_count", "comment_count")
        },
        "analysis": analysis.model_dump(),
    }
    (notes / f"{key}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = notes / f"{key}.md"
    md.write_text(render(record), encoding="utf-8")
    write_index(notes)
    return md


def load(notes: Path, key: str) -> dict[str, Any]:
    return json.loads((notes / f"{key}.json").read_text(encoding="utf-8"))


def transcript(notes: Path, rec: dict[str, Any]) -> str | None:
    f = rec.get("transcript_file")
    return (notes / f).read_text(encoding="utf-8") if f and (notes / f).exists() else None


def load_all(notes: Path) -> list[dict[str, Any]]:
    if not notes.is_dir():
        return []
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(notes.glob("*.json"))]
    return sorted(recs, key=lambda r: (r["analyzed_on"], r["id"]))


def mark_proposed(notes: Path, ids: list[str], proposal_name: str) -> None:
    for i in ids:
        p = notes / f"{i}.json"
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec["proposed_in"] = proposal_name
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_index(notes)


def render(rec: dict[str, Any]) -> str:
    a = rec["analysis"]
    m = rec["meta"]
    stats = " · ".join(
        f"{label} {m[k]:,}" for label, k in (("조회", "view_count"), ("좋아요", "like_count"), ("댓글", "comment_count"))
        if isinstance(m.get(k), int)
    )
    out = [
        f"# {a['title']}",
        "",
        f"- 원본: <{rec['url']}>",
        f"- 채널: {m.get('uploader') or '-'} · 업로드 {m.get('upload_date') or '-'} · {stats or '수치 없음'}",
        f"- 분석: {rec['analyzed_on']} · {rec['model']} · 대본 출처 {rec['transcript_source']}"
        + (f" · [전체 대본]({rec['transcript_file']})" if rec.get("transcript_file") else ""),
        f"- 형식: {a['format']} · 태그: {', '.join(a['tags'])}",
        "",
        "## 요약",
        a["summary"],
        "",
        "## 훅 (첫 3초)",
        a["hook"],
        "",
        "## 프로젝트에 주는 시사점",
    ]
    if a["implications"]:
        out += ["| 우선 | 대상 | 할 일 | 이유 |", "|---|---|---|---|"]
        for i in sorted(a["implications"], key=lambda x: ["high", "medium", "low"].index(x["priority"])):
            out.append(f"| {_PRIO[i['priority']]} | {_cell(i['target'])} | {_cell(i['action'])} | {_cell(i['rationale'])} |")
    else:
        out.append("반영할 것 없음.")
    out += ["", "## 렌즈 질문별 답"]
    for f in a["lens_findings"]:
        out += [f"**{f['question']}** — 확신 {_PRIO[f['confidence']]}", "", f"{f['answer']}", "", f"> 근거: {f['evidence']}", ""]
    out += ["## 주요 장면"] + [f"- `{x['timestamp']}` {x['what']}" for x in a["moments"]]
    out += ["", "## 화면에서 본 것"] + [f"- {x}" for x in a["visual_observations"]]
    out += ["", "## 반응·타깃 신호"] + [f"- {x}" for x in a["audience_signals"]]
    if a["claims_and_data"]:
        out += ["", "## 영상이 제시한 주장·수치"] + [f"- {x}" for x in a["claims_and_data"]]
    return "\n".join(out) + "\n"


def write_index(notes: Path) -> None:
    recs = load_all(notes)
    lines = [
        "# 영상 분석 노트",
        "",
        "`vi analyze`가 자동으로 만드는 목록입니다. 직접 고치지 마세요.",
        "",
        "| 분석일 | 노트 | 플랫폼 | 핵심 시사점 | 반영 |",
        "|---|---|---|---|---|",
    ]
    for r in reversed(recs):
        a = r["analysis"]
        top = next((i["action"] for i in a["implications"] if i["priority"] == "high"), None) or (
            a["implications"][0]["action"] if a["implications"] else "-"
        )
        lines.append(
            f"| {r['analyzed_on']} | [{_cell(a['title'])}]({r['id']}.md) | {r['platform']} | "
            f"{_cell(top)} | {r['proposed_in'] or '대기'} |"
        )
    (notes / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
