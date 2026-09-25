"""URL 목록 → (받기 → 키프레임 → 대본 → 분석 → 노트). analyze 와 research 가 함께 쓴다."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import notes as notes_mod
from .analyze import analyze
from .fetch import FetchOptions, fetch, metadata_digest, peek_key
from .lens import Lens
from .media import extract_audio, keyframes
from .transcript import parse_vtt, pick_subtitle, whisper


@dataclass
class RunOptions:
    model: str
    frames: int = 16
    comments: int = 0
    whisper: bool = True
    force: bool = False


def run(urls: list[str], lens: Lens, fetch_opts: FetchOptions, ro: RunOptions) -> tuple[list[tuple[str, str]], int]:
    """(URL, 노트 id) 목록 — 새로 만든 것 + 이미 있던 것 — 과 실패 수를 돌려준다."""
    keys: list[tuple[str, str]] = []
    failed = 0
    for n, url in enumerate(urls, 1):
        print(f"\n({n}/{len(urls)}) {url}")
        try:
            key = None if ro.force else peek_key(url, fetch_opts)
            if key and notes_mod.exists(lens.notes_path, key):
                print(f"  = 이미 분석함 ({key}). 다시 하려면 --force")
                keys.append((url, key))
                continue
            src = fetch(url, fetch_opts)
            if not ro.force and notes_mod.exists(lens.notes_path, src.key):
                print(f"  = 이미 분석함 ({src.key}). 다시 하려면 --force")
                keys.append((url, src.key))
                continue
            print(f"  ↓ {src.info.get('title', '')[:70]}")
            tmp = fetch_opts.workdir / src.key
            frames = keyframes(src.video_path, tmp / "frames", ro.frames)
            print(f"  ▣ 키프레임 {len(frames)}장")

            sub = pick_subtitle(src.subtitle_paths)
            transcript, t_src = None, "없음"
            if sub:
                transcript, t_src = parse_vtt(sub), f"자막({sub.name.split('.')[-2]})"
            elif ro.whisper:
                audio = extract_audio(src.video_path, tmp / "audio.wav")
                if audio:
                    transcript = whisper(audio, None)
                    t_src = "whisper" if transcript else "없음"
            print(f"  ✎ 대본: {t_src}" + (f" ({len(transcript):,}자)" if transcript else ""))

            result = analyze(
                lens=lens,
                url=src.url,
                metadata=metadata_digest(src.info, ro.comments),
                transcript=transcript,
                frames=frames,
                model=ro.model,
            )
            md = notes_mod.save(
                lens.notes_path, key=src.key, url=src.url, platform=src.platform,
                info=src.info, analysis=result, model=ro.model,
                transcript_source=t_src, transcript=transcript,
            )
            keys.append((url, src.key))
            print(f"  ✓ {_rel(md, lens.root)} — 시사점 {len(result.implications)}건")
        except KeyboardInterrupt:
            raise
        except Exception as e:  # 한 편이 실패해도 나머지는 계속한다
            failed += 1
            print(f"  ✗ 실패: {e}", file=sys.stderr)
    return keys, failed


def _rel(p: Path, root: Path) -> Path:
    try:
        return p.relative_to(root)
    except ValueError:
        return p
