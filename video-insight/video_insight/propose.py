"""쌓인 분석 노트 → 프로젝트 문서 반영안 (수정 + 브랜치 + PR)."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from . import notes as notes_mod
from .claude import ask
from .lens import Lens
from .schemas import Edit, Proposal

SYSTEM = """당신은 프로젝트 문서의 편집자다. 영상 분석 노트들에서 나온 인사이트를
프로젝트 문서에 반영할 구체적인 수정안을 만든다.

원칙:
- 문서의 기존 논지·톤·구조·마크업을 지킨다. 새 주장은 노트의 근거가 있을 때만 넣는다.
- 여러 노트가 같은 방향을 가리키면 더 강하게, 한 편뿐이면 조심스럽게 쓴다.
- 영상 속 수치는 "영상에서 제시된" 것으로 다루고, 문서의 기존 데이터와 충돌하면 수정하지 말고
  open_questions에 적는다.
- 각 edit의 old_string은 해당 파일에 정확히 한 번 나오는 원문 그대로여야 한다(공백·태그 포함).
  짧게 잡되 유일하도록 앞뒤 문맥을 포함한다. new_string은 old_string을 대체할 전체 텍스트다.
- 억지로 고치지 않는다. 반영할 가치가 없으면 edits를 비우고 이유를 not_applied에 쓴다.
- 결과는 {language}로 쓴다."""


def pending(lens: Lens, include_all: bool, only: list[str] | None) -> list[dict[str, Any]]:
    recs = notes_mod.load_all(lens.notes_path)
    if only:
        return [r for r in recs if r["id"] in only]
    return recs if include_all else [r for r in recs if not r.get("proposed_in")]


def build_proposal(lens: Lens, recs: list[dict[str, Any]], model: str) -> Proposal:
    ctx = lens.read_context()
    if not ctx:
        raise SystemExit("context_files 에 읽을 수 있는 파일이 없습니다. .video-insight.toml 을 확인하세요.")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": f"## 프로젝트: {lens.name}\n{lens.description}"},
    ]
    if lens.propose_guidelines:
        content.append({"type": "text", "text": f"## 반영 지침\n{lens.propose_guidelines}"})
    for rel, text in ctx.items():
        content.append({"type": "text", "text": f"## 파일: {rel}\n```\n{text}\n```"})
    slim = [{"id": r["id"], "url": r["url"], "meta": r["meta"], "analysis": r["analysis"]} for r in recs]
    content.append({
        "type": "text",
        "text": "## 영상 분석 노트\n```json\n" + json.dumps(slim, ensure_ascii=False, indent=1) + "\n```",
    })
    content.append({"type": "text", "text": "위 노트를 근거로 파일 수정안을 만들어라."})
    return ask(
        system=SYSTEM.format(language="한국어" if lens.language == "ko" else lens.language),
        content=content,
        schema=Proposal,
        model=model,
    )


def apply_edits(lens: Lens, edits: list[Edit]) -> tuple[list[Edit], list[tuple[Edit, str]]]:
    """old_string이 파일에 정확히 한 번 있을 때만 바꾼다. 나머지는 이유와 함께 돌려준다."""
    applied: list[Edit] = []
    skipped: list[tuple[Edit, str]] = []
    allowed = set(lens.context_files)
    for e in edits:
        if e.file not in allowed:
            skipped.append((e, "context_files 밖의 파일"))
            continue
        p = lens.root / e.file
        text = p.read_text(encoding="utf-8")
        n = text.count(e.old_string)
        if n != 1:
            skipped.append((e, f"원문이 {n}번 발견됨 (정확히 1번이어야 함)"))
            continue
        p.write_text(text.replace(e.old_string, e.new_string, 1), encoding="utf-8")
        applied.append(e)
    return applied, skipped


def write_record(
    lens: Lens, name: str, prop: Proposal, recs: list[dict[str, Any]],
    applied: list[Edit], skipped: list[tuple[Edit, str]],
) -> Path:
    d = lens.notes_path / "proposals"
    d.mkdir(parents=True, exist_ok=True)
    titles = {r["id"]: r["analysis"]["title"] for r in recs}
    out = [
        f"# 영상 인사이트 반영안 — {name}",
        "",
        prop.summary,
        "",
        "## 근거 영상",
        *[f"- [{titles[r['id']]}](../{r['id']}.md) — <{r['url']}>" for r in recs],
        "",
        f"## 반영한 수정 ({len(applied)})",
    ]
    for e in applied:
        out += [f"### `{e.file}`", f"{e.reason}  ", f"근거: {', '.join(e.sources)}", "",
                "```diff", *[f"- {l}" for l in e.old_string.splitlines()],
                *[f"+ {l}" for l in e.new_string.splitlines()], "```", ""]
    if skipped:
        out += [f"## 적용하지 못한 수정 ({len(skipped)})"]
        for e, why in skipped:
            out += [f"- `{e.file}` — {why}: {e.reason}"]
        out.append("")
    if prop.not_applied:
        out += ["## 문서에 옮기지 않은 인사이트"] + [f"- {x}" for x in prop.not_applied] + [""]
    if prop.open_questions:
        out += ["## 확인이 필요한 것"] + [f"- {x}" for x in prop.open_questions] + [""]
    path = d / f"{name}.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], check=check, capture_output=True, text=True)


def open_pr(lens: Lens, name: str, record: Path, n_videos: int, n_edits: int, base: str | None) -> None:
    root = lens.root
    if git(root, "status", "--porcelain", check=False).returncode != 0:
        print("git 저장소가 아니라서 PR 단계는 건너뜁니다.")
        return
    branch = f"video-insight/{name}"
    git(root, "checkout", "-b", branch)
    git(root, "add", "--", lens.notes_dir, *lens.context_files)
    title = f"영상 인사이트 반영: 영상 {n_videos}편, 수정 {n_edits}건"
    git(root, "commit", "-m", f"{title}\n\n{record.relative_to(root)} 참고")
    git(root, "push", "-u", "origin", branch)
    if shutil.which("gh"):
        cmd = ["gh", "pr", "create", "--title", title, "--body-file", str(record), "--head", branch]
        if base:
            cmd += ["--base", base]
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
    else:
        print(f"브랜치 {branch} 를 푸시했습니다. gh CLI가 없어 PR은 GitHub에서 직접 열어 주세요.")


def timestamp_name() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M")
