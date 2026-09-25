"""주제 하나 → 영상을 알아서 찾고, 고르고, 읽고, 보고서로 묶는다.

  1. 검색 계획   Claude가 주제와 렌즈를 보고 검색어를 만든다
  2. 찾기       유튜브: yt-dlp 검색 (API 키 불필요)
                인스타 릴스·틱톡: Claude 웹 검색으로 영상 URL 수집
                렌즈에 등록한 계정·채널의 최근 영상
  3. 고르기     후보의 제목·조회수·길이를 보고 Claude가 볼 가치가 있는 것만 선별
  4. 읽기       pipeline.run — 영상마다 대본(.txt)과 분석 노트(.md)
  5. 보고서     모든 노트와 대본을 근거로 인용·타임스탬프가 붙은 리서치 보고서
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from . import notes as notes_mod
from .claude import ask, client
from .fetch import FetchOptions, _base_opts, expand
from .lens import Lens
from .schemas import ResearchReport, Screening, SearchPlan

_PRIO = {"high": "상", "medium": "중", "low": "하"}

SOCIAL_URL = re.compile(
    r"https?://(?:www\.)?(?:"
    r"instagram\.com/(?:reels?|p|tv)/[A-Za-z0-9_-]+"
    r"|tiktok\.com/@[\w.-]+/video/\d+"
    r")"
)


@dataclass
class Candidate:
    url: str
    platform: str
    title: str = ""
    channel: str = ""
    views: int | None = None
    duration: float | None = None
    upload_date: str | None = None
    found_by: str = ""

    def line(self, i: int) -> str:
        bits = [f"[{i}] ({self.platform}) {self.title or '(제목 없음)'}"]
        if self.channel:
            bits.append(f"채널 {self.channel}")
        if self.views is not None:
            bits.append(f"조회 {self.views:,}")
        if self.duration:
            bits.append(f"{int(self.duration) // 60}분 {int(self.duration) % 60}초")
        if self.upload_date:
            bits.append(f"업로드 {self.upload_date}")
        bits.append(f"검색어 '{self.found_by}'")
        return " · ".join(bits)


# ── 1. 검색 계획 ────────────────────────────────────────────────


def plan(topic: str, lens: Lens, model: str, platforms: set[str]) -> SearchPlan:
    want = []
    if "youtube" in platforms:
        want.append("유튜브 검색어 4~6개")
    if platforms & {"instagram", "tiktok"}:
        want.append("인스타 릴스·틱톡 영상을 찾을 웹 검색어 3~5개")
    return ask(
        system=(
            "당신은 영상 리서치 설계자다. 리서치 질문에 답할 영상을 찾기 위한 검색어를 만든다. "
            "실제 사용자가 올리는 영상 제목·해시태그에 나올 법한 말로 쓰고, 한 가지 표현에 몰리지 않게 "
            "각도를 나눈다(제품, 상황, 사람, 문제). 필요 없는 플랫폼의 목록은 비워 둔다."
        ),
        content=[{
            "type": "text",
            "text": f"## 프로젝트: {lens.name}\n{lens.description}\n\n## 리서치 질문\n{topic}\n\n"
                    f"## 필요한 것\n" + "\n".join(f"- {w}" for w in want),
        }],
        schema=SearchPlan,
        model=model,
        effort="medium",
    )


# ── 2. 찾기 ─────────────────────────────────────────────────────


def search_youtube(query: str, n: int, opts: FetchOptions, recent: bool) -> list[Candidate]:
    prefix = "ytsearchdate" if recent else "ytsearch"
    with YoutubeDL(_base_opts(opts) | {"extract_flat": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(f"{prefix}{n}:{query}", download=False)
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append(Candidate(
            url=e.get("url") if str(e.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={e['id']}",
            platform="youtube",
            title=e.get("title") or "",
            channel=e.get("channel") or e.get("uploader") or "",
            views=e.get("view_count"),
            duration=e.get("duration"),
            upload_date=e.get("upload_date"),
            found_by=query,
        ))
    return out


def search_social(queries: list[str], platforms: set[str], model: str) -> list[Candidate]:
    """Claude 웹 검색(서버 도구)으로 인스타 릴스·틱톡 영상 URL을 모은다."""
    domains = [d for p, d in (("instagram", "instagram.com"), ("tiktok", "tiktok.com")) if p in platforms]
    prompt = (
        "아래 검색어로 웹 검색을 해서 개별 영상 페이지 URL을 최대한 많이 찾아라 "
        "(instagram.com/reel/..., instagram.com/p/..., tiktok.com/@계정/video/...). "
        "계정 메인 페이지가 아니라 개별 영상이어야 한다. 찾은 URL과 한 줄 설명을 목록으로 답하라.\n\n"
        + "\n".join(f"- {q}" for q in queries)
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tools = [{
        "type": "web_search_20260209", "name": "web_search",
        "max_uses": max(3, len(queries) * 2), "allowed_domains": domains,
    }]
    found: dict[str, Candidate] = {}
    for _ in range(4):  # pause_turn 이어받기 상한
        resp = client().beta.messages.create(
            model=model, max_tokens=16000, tools=tools, messages=messages,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        for block in resp.content:
            if block.type == "web_search_tool_result" and isinstance(block.content, list):
                for r in block.content:
                    _add_social(found, getattr(r, "url", ""), getattr(r, "title", ""), "웹 검색")
            elif block.type == "text":
                for m in SOCIAL_URL.finditer(block.text):
                    _add_social(found, m.group(0), "", "웹 검색")
        if resp.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": resp.content}]
    return list(found.values())


def _add_social(found: dict[str, Candidate], url: str, title: str, by: str) -> None:
    m = SOCIAL_URL.search(url or "")
    if not m:
        return
    u = m.group(0).replace("/reels/", "/reel/")
    if u not in found:
        found[u] = Candidate(url=u, platform="instagram" if "instagram" in u else "tiktok", title=title, found_by=by)


def from_accounts(accounts: list[str], per_account: int, opts: FetchOptions) -> list[Candidate]:
    out = []
    for acc in accounts:
        try:
            for u in expand(acc, opts, per_account):
                out.append(Candidate(url=u, platform=_platform(u), found_by=f"계정 {acc}"))
        except Exception as e:
            print(f"  ! 계정 목록을 읽지 못함: {acc} — {e}", file=sys.stderr)
    return out


def _platform(url: str) -> str:
    for p in ("youtube", "instagram", "tiktok"):
        if p in url or (p == "youtube" and "youtu.be" in url):
            return p
    return "web"


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    seen: dict[str, Candidate] = {}
    for c in cands:
        key = re.sub(r"[?&](si|feature|igsh)=[^&]*", "", c.url.rstrip("/"))
        seen.setdefault(key, c)
    return list(seen.values())


# ── 3. 고르기 ───────────────────────────────────────────────────


def screen(topic: str, lens: Lens, cands: list[Candidate], k: int, model: str) -> list[tuple[Candidate, str]]:
    if len(cands) <= k:
        return [(c, "후보 수가 선별 기준 이하") for c in cands]
    listing = "\n".join(c.line(i) for i, c in enumerate(cands))
    result = ask(
        system=(
            "당신은 영상 리서처다. 후보 목록에서 리서치 질문에 실제 근거를 줄 영상을 고른다. "
            "광고·재업로드·주제와 무관한 것은 빼고, 관점이 겹치지 않게 다양하게 고른다. "
            "조회수는 참고만 하고, 작은 채널이라도 실제 사용자의 목소리면 우선한다. "
            "제목이 없는 인스타·틱톡 후보는 판단 근거가 적으니 몇 개만 섞는다."
        ),
        content=[{
            "type": "text",
            "text": f"## 프로젝트: {lens.name}\n{lens.description}\n\n## 리서치 질문\n{topic}\n\n"
                    f"## 후보 {len(cands)}개\n{listing}\n\n최대 {k}개를 골라라.",
        }],
        schema=Screening,
        model=model,
        effort="medium",
    )
    picked = []
    for p in result.picks[:k]:
        if 0 <= p.index < len(cands) and all(c is not cands[p.index] for c, _ in picked):
            picked.append((cands[p.index], p.reason))
    print(f"  선별 기준: {result.note}")
    return picked


# ── 5. 보고서 ───────────────────────────────────────────────────


def report(topic: str, lens: Lens, keys: list[str], model: str) -> ResearchReport:
    blocks: list[dict[str, Any]] = [{
        "type": "text",
        "text": f"## 프로젝트: {lens.name}\n{lens.description}\n\n## 리서치 질문\n{topic}",
    }]
    for key in keys:
        rec = notes_mod.load(lens.notes_path, key)
        body = {"id": rec["id"], "url": rec["url"], "meta": rec["meta"], "analysis": rec["analysis"]}
        text = f"## 영상 노트 {key}\n```json\n{json.dumps(body, ensure_ascii=False)}\n```"
        t = notes_mod.transcript(lens.notes_path, rec)
        if t:
            text += f"\n### 대본\n{t}"
        blocks.append({"type": "text", "text": text})
    blocks.append({"type": "text", "text": "위 영상들을 근거로 리서치 보고서를 써라."})
    return ask(
        system=(
            "당신은 리서치 보고서를 쓰는 분석가다. 여러 영상의 대본과 분석 노트를 종합해 질문에 답한다.\n"
            "- 모든 발견에는 근거 영상(note_id)과 타임스탬프, 짧은 인용을 붙인다. 근거 없는 일반론은 쓰지 않는다.\n"
            "- 한 편에서만 나온 이야기는 strength를 low로 두고, 여러 편이 독립적으로 말하면 high.\n"
            "- 영상 속 수치는 영상의 주장으로 다룬다.\n"
            "- 프로젝트의 가설과 부딪히는 증거는 숨기지 말고 disagreements에 적는다.\n"
            f"- {'한국어' if lens.language == 'ko' else lens.language}로 쓴다."
        ),
        content=blocks,
        schema=ResearchReport,
        model=model,
    )


def write_report(
    lens: Lens, topic: str, rep: ResearchReport, keys: list[str],
    picks: dict[str, str], search_note: str,
) -> Path:
    d = lens.notes_path / "reports"
    d.mkdir(parents=True, exist_ok=True)
    recs = {k: notes_mod.load(lens.notes_path, k) for k in keys}

    def ref(e) -> str:
        r = recs.get(e.note_id)
        label = r["analysis"]["title"] if r else e.note_id
        ts = f" `{e.timestamp}`" if e.timestamp else ""
        return f"[{label}](../{e.note_id}.md){ts} — “{e.quote}”"

    out = [
        f"# {rep.title}",
        "",
        f"> 리서치 질문: {topic}  ",
        f"> {date.today().isoformat()} · 영상 {len(keys)}편 · {search_note}",
        "",
        "## 답",
        rep.answer,
        "",
        "## 발견",
    ]
    for i, f in enumerate(rep.findings, 1):
        out += [f"### {i}. {f.finding}", f"근거 강도: {_PRIO[f.strength]}", ""]
        out += [f"- {ref(e)}" for e in f.evidence] + [""]
    if rep.content_patterns:
        out += ["## 잘 되는 영상의 공통점"] + [f"- {x}" for x in rep.content_patterns] + [""]
    if rep.disagreements:
        out += ["## 엇갈리는 지점"] + [f"- {x}" for x in rep.disagreements] + [""]
    if rep.implications:
        out += ["## 프로젝트에 주는 시사점", "| 우선 | 대상 | 할 일 | 이유 |", "|---|---|---|---|"]
        for x in sorted(rep.implications, key=lambda x: ["high", "medium", "low"].index(x.priority)):
            out.append(f"| {_PRIO[x.priority]} | {_cell(x.target)} | {_cell(x.action)} | {_cell(x.rationale)} |")
        out.append("")
    if rep.gaps:
        out += ["## 영상으로 확인하지 못한 것"] + [f"- {x}" for x in rep.gaps] + [""]
    if rep.next_queries:
        out += ["## 다음에 파볼 것"] + [f"- `{x}`" for x in rep.next_queries] + [""]
    out += ["## 읽은 영상", "| 노트 | 원본 | 대본 | 고른 이유 |", "|---|---|---|---|"]
    for k, r in recs.items():
        tf = f"[대본]({'../' + r['transcript_file']})" if r.get("transcript_file") else "없음"
        out.append(f"| [{_cell(r['analysis']['title'])}](../{k}.md) | <{r['url']}> | {tf} | {_cell(picks.get(k, '이전에 분석함'))} |")
    slug = re.sub(r"[^\w가-힣]+", "-", topic).strip("-")[:40]
    path = d / f"{date.today().isoformat()}-{slug}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
