"""vi — 영상을 읽고 분석해 프로젝트에 반영한다.

  vi analyze <URL|파일> ...        영상 분석 → 노트 저장
  vi propose                       쌓인 노트 → 문서 수정안 (+ --pr 로 브랜치·PR)
  vi index                         노트 목록 다시 만들기
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from . import notes as notes_mod
from .claude import DEFAULT_MODEL


def _fetch_opts(args: argparse.Namespace):
    from .fetch import FetchOptions

    work_root = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="video-insight-"))
    return FetchOptions(
        workdir=work_root,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies,
        max_comments=args.comments,
    )


def _run_opts(args: argparse.Namespace):
    from .pipeline import RunOptions

    return RunOptions(
        model=args.model, frames=args.frames, comments=args.comments,
        whisper=not args.no_whisper, force=args.force,
    )


def _cleanup(args: argparse.Namespace, work_root: Path) -> None:
    if args.keep_media or args.workdir:
        print(f"\n원본·프레임 보관 위치: {work_root}")
    else:
        shutil.rmtree(work_root, ignore_errors=True)


def cmd_analyze(args: argparse.Namespace) -> int:
    from .fetch import expand
    from .lens import load_lens
    from .media import require_ffmpeg
    from .pipeline import run

    require_ffmpeg()
    lens = load_lens(Path(args.project))
    inputs = list(args.urls)
    if args.from_file:
        inputs += [l.strip() for l in Path(args.from_file).read_text().splitlines()
                   if l.strip() and not l.lstrip().startswith("#")]
    if not inputs:
        print("분석할 URL이나 파일을 주세요.", file=sys.stderr)
        return 2

    opts = _fetch_opts(args)
    urls: list[str] = []
    failed = 0
    for u in inputs:
        try:
            urls += expand(u, opts, args.limit)
        except Exception as e:
            failed += 1
            print(f"✗ 목록을 읽지 못함: {u} — {e}", file=sys.stderr)
    print(f"[{lens.name}] 영상 {len(urls)}편")

    _, run_failed = run(urls, lens, opts, _run_opts(args))
    _cleanup(args, opts.workdir)
    print(f"\n완료. 노트 목록: {(lens.notes_path / 'INDEX.md').relative_to(lens.root)}")
    return 1 if failed or run_failed else 0


def cmd_research(args: argparse.Namespace) -> int:
    from . import research as rs
    from .lens import load_lens
    from .media import require_ffmpeg
    from .pipeline import run

    require_ffmpeg()
    lens = load_lens(Path(args.project))
    topic = " ".join(args.topic)
    platforms = set(args.platforms.split(","))
    opts = _fetch_opts(args)
    print(f"[{lens.name}] 리서치: {topic}")

    print("\n① 검색 계획")
    sp = rs.plan(topic, lens, args.model, platforms)
    for q in sp.youtube_queries + sp.social_queries:
        print(f"  · {q}")

    print("\n② 영상 찾기")
    cands: list[rs.Candidate] = []
    if "youtube" in platforms:
        for q in sp.youtube_queries:
            try:
                got = rs.search_youtube(q, args.per_query, opts, args.recent)
                cands += got
                print(f"  유튜브 '{q}' → {len(got)}")
            except Exception as e:
                print(f"  ! 유튜브 검색 실패 '{q}': {e}", file=sys.stderr)
    if platforms & {"instagram", "tiktok"} and sp.social_queries:
        try:
            got = rs.search_social(sp.social_queries, platforms, args.model)
            cands += got
            print(f"  인스타·틱톡 웹 검색 → {len(got)}")
        except Exception as e:
            print(f"  ! 웹 검색 실패: {e}", file=sys.stderr)
    if lens.research_accounts and not args.no_accounts:
        got = rs.from_accounts(lens.research_accounts, args.per_query, opts)
        cands += got
        print(f"  등록 계정 {len(lens.research_accounts)}곳 → {len(got)}")
    if args.max_minutes:
        cands = [c for c in cands if not c.duration or c.duration <= args.max_minutes * 60]
    cands = rs.dedupe(cands)
    if not cands:
        print("찾은 영상이 없습니다. 주제를 바꾸거나 --platforms 를 확인하세요.", file=sys.stderr)
        _cleanup(args, opts.workdir)
        return 1

    print(f"\n③ 고르기 — 후보 {len(cands)}개 중 최대 {args.max}개")
    picks = rs.screen(topic, lens, cands, args.max, args.model)
    for c, why in picks:
        print(f"  ✓ {c.title[:50] or c.url} — {why}")

    print("\n④ 영상 읽기")
    done, failed = run([c.url for c, _ in picks], lens, opts, _run_opts(args))
    _cleanup(args, opts.workdir)
    if not done:
        print("분석에 성공한 영상이 없어 보고서를 만들지 못했습니다.", file=sys.stderr)
        return 1

    print(f"\n⑤ 보고서 — 영상 {len(done)}편")
    reasons = {c.url: why for c, why in picks}
    keys = list(dict.fromkeys(k for _, k in done))
    rep = rs.report(topic, lens, keys, args.model)
    note = f"후보 {len(cands)}개 중 선별" + (f", 실패 {failed}편" if failed else "")
    path = rs.write_report(lens, topic, rep, keys, {k: reasons.get(u, "") for u, k in done}, note)
    print(f"\n{rep.answer}\n\n보고서: {path.relative_to(lens.root)}")
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    from .lens import load_lens
    from .propose import apply_edits, build_proposal, open_pr, pending, timestamp_name, write_record

    lens = load_lens(Path(args.project))
    recs = pending(lens, args.all, args.only)
    if not recs:
        print("반영 대기 중인 노트가 없습니다. (이미 반영한 것까지 보려면 --all)")
        return 0
    print(f"[{lens.name}] 노트 {len(recs)}편으로 반영안을 만듭니다…")
    prop = build_proposal(lens, recs, args.model)
    name = timestamp_name()

    if args.dry_run:
        print(f"\n{prop.summary}\n")
        for e in prop.edits:
            print(f"- {e.file}: {e.reason}")
        for q in prop.open_questions:
            print(f"? {q}")
        return 0

    applied, skipped = apply_edits(lens, prop.edits)
    record = write_record(lens, name, prop, recs, applied, skipped)
    notes_mod.mark_proposed(lens.notes_path, [r["id"] for r in recs], name)
    print(f"수정 {len(applied)}건 적용, {len(skipped)}건 건너뜀 → {record.relative_to(lens.root)}")
    if args.pr:
        open_pr(lens, name, record, len(recs), len(applied), args.base)
    else:
        print("변경 사항을 확인한 뒤 커밋하세요. 브랜치와 PR까지 자동으로 만들려면 --pr")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from .lens import load_lens

    lens = load_lens(Path(args.project))
    notes_mod.write_index(lens.notes_path)
    print(lens.notes_path / "INDEX.md")
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--project", default=".", help="프로젝트 루트 (.video-insight.toml 위치). 기본: 현재 폴더")
    common.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude 모델 (기본 {DEFAULT_MODEL}, 환경변수 VI_MODEL)")
    p = argparse.ArgumentParser(prog="vi", description="영상을 읽고 분석해 프로젝트에 반영한다.")
    sub = p.add_subparsers(dest="cmd", required=True)

    media = argparse.ArgumentParser(add_help=False)
    media.add_argument("--frames", type=int, default=16, help="키프레임 최대 장수 (기본 16)")
    media.add_argument("--comments", type=int, default=0, help="인기 댓글 N개 포함 (유튜브)")
    media.add_argument("--cookies-from-browser", metavar="BROWSER", help="인스타 등 로그인이 필요할 때: chrome, safari, firefox…")
    media.add_argument("--cookies", metavar="FILE", help="cookies.txt 파일")
    media.add_argument("--no-whisper", action="store_true", help="자막이 없어도 받아쓰기 하지 않음")
    media.add_argument("--force", action="store_true", help="이미 분석한 영상도 다시 분석")
    media.add_argument("--workdir", help="다운로드 작업 폴더 (지정하면 지우지 않음)")
    media.add_argument("--keep-media", action="store_true", help="영상·프레임을 지우지 않음")

    a = sub.add_parser("analyze", parents=[common, media], help="영상 분석 → 노트 저장")
    a.add_argument("urls", nargs="*", help="영상·재생목록·채널 URL 또는 로컬 영상 파일")
    a.add_argument("-f", "--from-file", help="URL 목록 파일 (한 줄에 하나, # 주석)")
    a.add_argument("--limit", type=int, help="재생목록·채널에서 최대 몇 편")
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("research", parents=[common, media], help="주제 → 영상을 알아서 찾아 읽고 보고서")
    s.add_argument("topic", nargs="+", help="리서치 질문 (예: 성인 발레인은 수업 전에 무엇을 걸치나)")
    s.add_argument("--max", type=int, default=8, help="읽을 영상 수 (기본 8)")
    s.add_argument("--per-query", type=int, default=12, help="검색어당 후보 수 (기본 12)")
    s.add_argument("--platforms", default="youtube,instagram,tiktok", help="쉼표로 구분 (기본 전부)")
    s.add_argument("--recent", action="store_true", help="유튜브를 최신순으로 검색")
    s.add_argument("--max-minutes", type=float, default=30, help="이보다 긴 영상은 제외 (기본 30분)")
    s.add_argument("--no-accounts", action="store_true", help="렌즈에 등록한 계정은 보지 않음")
    s.set_defaults(func=cmd_research)

    r = sub.add_parser("propose", parents=[common], help="쌓인 노트 → 문서 수정안")
    r.add_argument("--all", action="store_true", help="이미 반영한 노트까지 포함")
    r.add_argument("--only", nargs="+", metavar="ID", help="특정 노트 id만")
    r.add_argument("--dry-run", action="store_true", help="수정하지 않고 요약만 출력")
    r.add_argument("--pr", action="store_true", help="브랜치 만들고 커밋·푸시·PR 생성 (gh CLI)")
    r.add_argument("--base", help="PR 대상 브랜치")
    r.set_defaults(func=cmd_propose)

    i = sub.add_parser("index", parents=[common], help="노트 목록(INDEX.md) 다시 만들기")
    i.set_defaults(func=cmd_index)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
