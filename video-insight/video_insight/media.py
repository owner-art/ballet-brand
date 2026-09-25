"""ffmpeg로 키프레임과 음성을 뽑는다."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Frame:
    seconds: float
    path: Path

    @property
    def stamp(self) -> str:
        s = int(self.seconds)
        return f"{s // 60:02d}:{s % 60:02d}"


def require_ffmpeg() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if not shutil.which(b)]
    if missing:
        raise SystemExit(
            f"{', '.join(missing)} 가 필요합니다. "
            "macOS: brew install ffmpeg · Ubuntu: sudo apt install ffmpeg · Windows: winget install ffmpeg"
        )


def duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def keyframes(video: Path, outdir: Path, max_frames: int, width: int = 768) -> list[Frame]:
    """영상 전체에 고르게 퍼진 프레임을 뽑는다.

    짧은 릴스는 1~2초 간격, 긴 영상은 max_frames개로 나눈 간격이 된다.
    양 끝(검은 화면, 엔딩 카드)을 피하려고 구간 중앙에서 뽑는다.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    total = duration(video)
    n = max(1, min(max_frames, int(total // 1.5) or 1))
    step = total / n
    frames: list[Frame] = []
    for i in range(n):
        t = step * (i + 0.5)
        path = outdir / f"frame_{i:03d}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(path)],
            check=True,
        )
        if path.exists():
            frames.append(Frame(t, path))
    return frames


def extract_audio(video: Path, out: Path) -> Path | None:
    """음성 트랙을 16kHz 모노로 뽑는다. 음성이 없으면 None."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    ).stdout.strip()
    if not probe:
        return None
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)],
        check=True,
    )
    return out
