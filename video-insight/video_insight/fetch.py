"""영상 받기: yt-dlp로 유튜브·인스타·틱톡 등 1,000개 이상 사이트를 처리한다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

SUB_LANGS = ["ko", "ko-KR", "ko.*", "en", "en-US", "en.*"]


@dataclass
class Source:
    """분석할 영상 하나."""

    key: str  # "youtube-abc123" 처럼 플랫폼-id, 중복 판별에 쓴다
    url: str
    platform: str
    video_path: Path
    info: dict[str, Any]
    subtitle_paths: list[Path] = field(default_factory=list)


@dataclass
class FetchOptions:
    workdir: Path
    cookies_from_browser: str | None = None
    cookies_file: str | None = None
    max_comments: int = 0
    max_height: int = 720


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-").lower() or "video"


def _base_opts(opts: FetchOptions) -> dict[str, Any]:
    o: dict[str, Any] = {"quiet": True, "no_warnings": True, "noprogress": True}
    if opts.cookies_from_browser:
        o["cookiesfrombrowser"] = (opts.cookies_from_browser,)
    if opts.cookies_file:
        o["cookiefile"] = opts.cookies_file
    return o


def expand(url: str, opts: FetchOptions, limit: int | None) -> list[str]:
    """재생목록·채널 URL이면 개별 영상 URL로 펼친다. 단일 영상이면 그대로."""
    if Path(url).exists():
        return [url]
    o = _base_opts(opts) | {"extract_flat": "in_playlist", "skip_download": True}
    if limit:
        o["playlistend"] = limit
    with YoutubeDL(o) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries")
    if not entries:
        return [url]
    urls: list[str] = []
    for e in entries:
        if e is None:
            continue
        if e.get("_type") == "playlist" or e.get("entries"):
            # 채널 탭(동영상/쇼츠)처럼 한 겹 더 들어가야 하는 경우
            urls.extend(expand(e.get("url") or e.get("webpage_url"), opts, limit))
        else:
            urls.append(e.get("webpage_url") or e.get("url"))
        if limit and len(urls) >= limit:
            break
    return urls[:limit] if limit else urls


def peek_key(url: str, opts: FetchOptions) -> str | None:
    """다운로드 없이 플랫폼-id 키만 구한다 (이미 분석한 영상 건너뛰기용).

    단축 링크처럼 id를 미리 알 수 없으면 None — 그땐 받아본 뒤에 판단한다.
    """
    if Path(url).exists():
        return f"local-{_slug(Path(url).stem)}"
    with YoutubeDL(_base_opts(opts) | {"skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
    if not info.get("id") or info.get("_type") == "url":
        return None
    return f"{_slug(info.get('extractor_key') or 'web')}-{_slug(str(info['id']))}"


def fetch(url: str, opts: FetchOptions) -> Source:
    local = Path(url)
    if local.exists():
        return Source(
            key=f"local-{_slug(local.stem)}",
            url=str(local.resolve()),
            platform="local",
            video_path=local.resolve(),
            info={"title": local.stem},
        )

    out = opts.workdir
    out.mkdir(parents=True, exist_ok=True)
    o = _base_opts(opts) | {
        "outtmpl": str(out / "%(extractor_key)s-%(id)s.%(ext)s"),
        "format": f"bv*[height<={opts.max_height}]+ba/b[height<={opts.max_height}]/b",
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": SUB_LANGS,
        "subtitlesformat": "vtt/best",
        # vtt가 아닌 자막(srv3, ttml 등)도 vtt로 맞춘다
        "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "vtt"}],
        "ignoreerrors": False,
    }
    if opts.max_comments:
        o["getcomments"] = True
        o["extractor_args"] = {
            "youtube": {"max_comments": [str(opts.max_comments)], "comment_sort": ["top"]}
        }
    with YoutubeDL(o) as ydl:
        info = ydl.extract_info(url, download=True)
        info = ydl.sanitize_info(info)

    stem = f"{info['extractor_key']}-{info['id']}"
    videos = [
        p for p in out.glob(f"{glob_escape(stem)}.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    ]
    if not videos:
        raise RuntimeError(f"영상 파일을 찾지 못했습니다: {url}")
    subs = sorted(out.glob(f"{glob_escape(stem)}.*.vtt"))
    (out / f"{stem}.info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return Source(
        key=f"{_slug(info['extractor_key'])}-{_slug(str(info['id']))}",
        url=info.get("webpage_url") or url,
        platform=info["extractor_key"].lower(),
        video_path=videos[0],
        info=info,
        subtitle_paths=subs,
    )


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def metadata_digest(info: dict[str, Any], max_comments: int) -> str:
    """Claude에게 줄 메타데이터 요약 텍스트."""
    fields = [
        ("제목", info.get("title")),
        ("채널/계정", info.get("uploader") or info.get("channel")),
        ("업로드", info.get("upload_date")),
        ("길이(초)", info.get("duration")),
        ("조회수", info.get("view_count")),
        ("좋아요", info.get("like_count")),
        ("댓글수", info.get("comment_count")),
        ("구독자", info.get("channel_follower_count")),
        ("태그", ", ".join(info.get("tags") or [])[:500] or None),
    ]
    lines = [f"- {k}: {v}" for k, v in fields if v not in (None, "")]
    desc = (info.get("description") or "").strip()
    if desc:
        lines.append(f"- 설명:\n{desc[:3000]}")
    comments = info.get("comments") or []
    if comments and max_comments:
        top = sorted(comments, key=lambda c: c.get("like_count") or 0, reverse=True)[:max_comments]
        lines.append("- 인기 댓글:")
        lines += [f"  · ({c.get('like_count') or 0}♥) {(c.get('text') or '').strip()[:300]}" for c in top]
    return "\n".join(lines)
