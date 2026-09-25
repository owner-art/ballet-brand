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


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze
    from .fetch import FetchOptions, expand, fetch, metadata_digest, peek_key
    from .lens import load_lens
    from .media import extract_audio, keyframes, require_ffmpeg
    from .transcript import parse_vtt, pick_subtitle, whisper

    require_ffmpeg()
    lens = load_lens(Path(args.project))
    inputs = list(args.urls)
    if args.from_file:
        inputs += [l.strip() for l in Path(args.from_file).read_text().splitlines()
                   if l.strip() and not l.lstrip().startswith("#")]
    if not inputs:
        print("분석할 URL이나 파일을 주세요.", file=sys.stderr)
        return 2

    work_root = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="video-insight-"))
    opts = FetchOptions(
        workdir=work_root,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies,
        max_comments=args.comments,
    )

    urls: list[str] = []
    failed = 0
    for u in inputs:
        try:
            urls += expand(u, opts, args.limit)
        except Exception as e:
            failed += 1
            print(f"✗ 목록을 읽지 못함: {u} — {e}", file=sys.stderr)
    print(f"[{lens.name}] 영상 {len(urls)}편")

    for n, url in enumerate(urls, 1):
        print(f"\n({n}/{len(urls)}) {url}")
        try:
            key = None if args.force else peek_key(url, opts)
            if key and notes_mod.exists(lens.notes_path, key):
                print(f"  = 이미 분석함 ({key}). 다시 하려면 --force")
                continue
            src = fetch(url, opts)
            if not args.force and notes_mod.exists(lens.notes_path, src.key):
                print(f"  = 이미 분석함 ({src.key}). 다시 하려면 --force")
                continue
            print(f"  ↓ {src.info.get('title', '')[:70]}")
            tmp = work_root / src.key
            frames = keyframes(src.video_path, tmp / "frames", args.frames)
            print(f"  ▣ 키프레임 {len(frames)}장")

            sub = pick_subtitle(src.subtitle_paths)
            transcript, t_src = None, "없음"
            if sub:
                transcript, t_src = parse_vtt(sub), f"자막({sub.name.split('.')[-2]})"
            elif not args.no_whisper:
                audio = extract_audio(src.video_path, tmp / "audio.wav")
                if audio:
                    transcript = whisper(audio, None)
                    t_src = "whisper" if transcript else "없음"
            print(f"  ✎ 대본: {t_src}")

            result = analyze(
                lens=lens,
                url=src.url,
                metadata=metadata_digest(src.info, args.comments),
                transcript=transcript,
                frames=frames,
                model=args.model,
            )
            md = notes_mod.save(
                lens.notes_path, key=src.key, url=src.url, platform=src.platform,
                info=src.info, analysis=result, model=args.model, transcript_source=t_src,
            )
            print(f"  ✓ {md.relative_to(lens.root)} — 시사점 {len(result.implications)}건")
        except KeyboardInterrupt:
            raise
        except Exception as e:  # 한 편이 실패해도 나머지는 계속한다
            failed += 1
            print(f"  ✗ 실패: {e}", file=sys.stderr)

    if not args.workdir and not args.keep_media:
        shutil.rmtree(work_root, ignore_errors=True)
    elif args.keep_media:
        print(f"\n원본·프레임 보관 위치: {work_root}")
    print(f"\n완료. 노트 목록: {(lens.notes_path / 'INDEX.md').relative_to(lens.root)}")
    return 1 if failed else 0


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

    a = sub.add_parser("analyze", parents=[common], help="영상 분석 → 노트 저장")
    a.add_argument("urls", nargs="*", help="영상·재생목록·채널 URL 또는 로컬 영상 파일")
    a.add_argument("-f", "--from-file", help="URL 목록 파일 (한 줄에 하나, # 주석)")
    a.add_argument("--limit", type=int, help="재생목록·채널에서 최대 몇 편")
    a.add_argument("--frames", type=int, default=16, help="키프레임 최대 장수 (기본 16)")
    a.add_argument("--comments", type=int, default=0, help="인기 댓글 N개 포함 (유튜브)")
    a.add_argument("--cookies-from-browser", metavar="BROWSER", help="인스타 등 로그인이 필요할 때: chrome, safari, firefox…")
    a.add_argument("--cookies", metavar="FILE", help="cookies.txt 파일")
    a.add_argument("--no-whisper", action="store_true", help="자막이 없어도 받아쓰기 하지 않음")
    a.add_argument("--force", action="store_true", help="이미 분석한 영상도 다시 분석")
    a.add_argument("--workdir", help="다운로드 작업 폴더 (지정하면 지우지 않음)")
    a.add_argument("--keep-media", action="store_true", help="영상·프레임을 지우지 않음")
    a.set_defaults(func=cmd_analyze)

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
