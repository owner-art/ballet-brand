"""대본 만들기: 자막이 있으면 자막, 없으면 Whisper로 받아쓰기."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.\d{3}\s+-->")
_TAG = re.compile(r"<[^>]+>")


def pick_subtitle(paths: list[Path]) -> Path | None:
    """수동 한국어 > 자동 한국어 > 영어 순으로 고른다."""
    def rank(p: Path) -> int:
        lang = p.name.split(".")[-2]
        return (0 if lang.startswith("ko") else 2) + (1 if "orig" in lang or "auto" in lang else 0)
    return min(paths, key=rank) if paths else None


def parse_vtt(path: Path) -> str:
    """VTT를 `[mm:ss] 문장` 줄로 바꾼다. 자동 자막의 반복 줄은 제거한다."""
    lines: list[str] = []
    seen_last = ""
    stamp = "00:00"
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _TS.match(raw)
        if m:
            h, mnt, s = map(int, m.groups())
            total = h * 3600 + mnt * 60 + s
            stamp = f"{total // 60:02d}:{total % 60:02d}"
            continue
        text = _TAG.sub("", raw).strip()
        if not text or text == "WEBVTT" or text.startswith(("Kind:", "Language:", "NOTE")) or "-->" in text:
            continue
        if text == seen_last:
            continue
        seen_last = text
        lines.append(f"[{stamp}] {text}")
    return "\n".join(lines)


def whisper(audio: Path, language: str | None) -> str | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "  ! 자막이 없고 faster-whisper가 설치되어 있지 않아 음성 없이 화면만 분석합니다.\n"
            "    받아쓰기를 쓰려면: pip install 'video-insight[whisper]'",
            file=sys.stderr,
        )
        return None
    size = os.environ.get("VI_WHISPER_MODEL", "small")
    model = WhisperModel(size, device="auto", compute_type="auto")
    segments, _ = model.transcribe(str(audio), language=language, vad_filter=True)
    out = []
    for seg in segments:
        s = int(seg.start)
        out.append(f"[{s // 60:02d}:{s % 60:02d}] {seg.text.strip()}")
    return "\n".join(out)
