# video-insight

유튜브·인스타그램·틱톡 영상(그리고 로컬 영상 파일)을 **읽고, 분석하고, 내 프로젝트에 반영**하는 CLI.

```
URL ─▶ ① 받기 ─▶ ② 쪼개기 ─▶ ③ 대본 ─▶ ④ 분석 ─▶ 노트 누적 ─▶ ⑤ 반영안 + PR
       yt-dlp    ffmpeg       자막 or    Claude     research/     문서 수정 → 브랜치 → PR
                 키프레임     Whisper    (렌즈)     videos/
```

- **렌즈**: 프로젝트마다 `.video-insight.toml` 하나. "이 프로젝트 입장에서 영상을 어떻게 볼 것인가"를 정한다.
  같은 영상도 브랜드 프로젝트와 앱 프로젝트에서 뽑아낼 것이 다르다.
- **노트 누적**: 영상 한 편 = 노트 한 장(`.md` + `.json`). 이미 분석한 영상은 건너뛴다.
- **반영안 PR**: 쌓인 노트를 근거로 Claude가 문서 수정안을 만들고, 원문이 정확히 일치하는 수정만 적용한 뒤
  브랜치·커밋·PR까지 만든다. 무엇을 왜 바꿨는지는 `proposals/`에 기록된다.

## 설치

```bash
# 필요: Python 3.10+, ffmpeg
brew install ffmpeg            # macOS  (Ubuntu: sudo apt install ffmpeg)

pip install -e path/to/video-insight            # 기본
pip install -e "path/to/video-insight[whisper]" # 자막 없는 영상(릴스 등) 받아쓰기까지

export ANTHROPIC_API_KEY=sk-ant-...
```

`gh` CLI가 있으면 `--pr` 에서 PR까지 자동으로 연다 (`brew install gh && gh auth login`).

## 사용법

프로젝트 폴더에서 실행한다 (다른 곳에서는 `-C <프로젝트 경로>`).

```bash
# 영상 분석 → research/videos/ 에 노트
vi analyze https://www.youtube.com/watch?v=XXXX
vi analyze https://www.instagram.com/reel/XXXX/ --cookies-from-browser chrome
vi analyze https://www.tiktok.com/@user/video/XXXX
vi analyze ~/Downloads/class.mp4

# 채널·재생목록은 펼쳐서 한 편씩 (최근 N편)
vi analyze https://www.youtube.com/@channel/videos --limit 10
vi analyze -f urls.txt                     # 한 줄에 URL 하나, # 주석

# 유튜브 인기 댓글까지 근거로 쓰기
vi analyze <URL> --comments 30

# 쌓인 노트 → 문서 반영
vi propose --dry-run                       # 무엇을 바꿀지 요약만
vi propose                                 # 파일 수정 + proposals/ 기록 (커밋은 직접)
vi propose --pr                            # 브랜치 video-insight/<시각> + 커밋 + 푸시 + PR
```

### 결과물

```
research/videos/
  INDEX.md                        전체 목록 — 분석일, 핵심 시사점, 반영 여부
  youtube-XXXX.md                 사람이 읽는 노트: 요약·훅·시사점·렌즈 답·장면·반응
  youtube-XXXX.json               propose 단계의 입력 (원본 데이터)
  proposals/2026-09-25-1530.md    반영안 기록: 근거 영상, 적용한 diff, 못 옮긴 인사이트, 확인할 것
```

## 렌즈 쓰기

프로젝트 루트에 `.video-insight.toml`. 전체 예시는 [`examples/lens.example.toml`](examples/lens.example.toml).

```toml
name = "프로젝트 이름"
description = """프로젝트가 무엇이고 무엇을 믿는지. Claude가 영상을 볼 때의 배경."""
questions = [
  "영상마다 답을 찾을 질문 1",
  "질문 2",
]
context_files = ["index.html", "docs/strategy.md"]  # propose가 읽고 고칠 수 있는 파일
notes_dir = "research/videos"                        # 기본값

[propose]
guidelines = """문서를 고칠 때 지킬 것 (톤, 건드리지 말 부분, 출처 표기 방식)."""
```

좋은 렌즈의 요령:
- 질문은 **예/아니오가 아니라 관찰을 요구**하게 ("무엇을, 왜 걸치는가?").
- 프로젝트의 **가설을 질문으로** 넣으면 영상이 지지하는지 반박하는지 매번 체크된다.
- `context_files`에는 영상 근거로 바뀌어도 되는 문서만. 수치 근거가 따로 있는 문서는 `guidelines`에서 보호한다.

## 옵션과 비용

| 항목 | 기본 | 설명 |
|---|---|---|
| `--model` / `VI_MODEL` | `claude-opus-5` | 분석·반영에 쓸 Claude 모델 |
| `--frames` | 16 | 키프레임 최대 장수. 영상 길이에 고르게 분포 (짧은 릴스는 약 1.5초 간격) |
| `VI_WHISPER_MODEL` | `small` | 받아쓰기 모델 크기 (`base`, `small`, `medium`, `large-v3`) |

한 편당 입력은 대략 키프레임 16장(약 1.5만 토큰) + 대본 + 메타데이터다.
Claude가 안전 정책으로 거절하는 경우를 대비해 서버측 fallback(`fallbacks: "default"`)을 켜 두었다.

## 주의

- 인스타그램은 로그인 없이 막히는 일이 잦다. `--cookies-from-browser chrome`(또는 `safari`, `firefox`)으로 이미 로그인된 브라우저 쿠키를 쓴다.
- 영상은 분석 후 지운다(`--keep-media`로 보관). 노트에는 원본 URL과 분석 결과만 남는다.
- 다운로드는 각 플랫폼 약관과 저작권의 범위 안에서, 리서치 목적으로만 쓴다.

## 개발

```bash
pip install -e . pytest
pytest            # API 호출 없이 전체 파이프라인 검증 (ffmpeg 필요)
```
