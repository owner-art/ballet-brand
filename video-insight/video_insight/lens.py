"""프로젝트 렌즈: 각 프로젝트 루트의 `.video-insight.toml`.

렌즈는 "이 프로젝트 입장에서 영상을 어떻게 볼 것인가"를 정한다.
같은 영상도 브랜드 프로젝트와 앱 프로젝트에서는 뽑아낼 것이 다르다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LENS_FILENAME = ".video-insight.toml"


@dataclass
class Lens:
    root: Path
    name: str
    description: str
    questions: list[str] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)
    notes_dir: str = "research/videos"
    language: str = "ko"
    propose_guidelines: str = ""

    @property
    def notes_path(self) -> Path:
        return self.root / self.notes_dir

    def read_context(self, max_chars_per_file: int = 120_000) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in self.context_files:
            p = self.root / rel
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_chars_per_file:
                raise SystemExit(
                    f"{rel} 이(가) {len(text):,}자로 너무 큽니다. "
                    f"context_files에서 빼거나 파일을 나눠 주세요."
                )
            out[rel] = text
        return out


def load_lens(project: Path) -> Lens:
    project = project.resolve()
    path = project / LENS_FILENAME
    if not path.is_file():
        raise SystemExit(
            f"{path} 가 없습니다. examples/lens.example.toml 을 참고해 만들어 주세요."
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    propose = data.get("propose", {})
    return Lens(
        root=project,
        name=data["name"],
        description=data.get("description", "").strip(),
        questions=list(data.get("questions", [])),
        context_files=list(data.get("context_files", [])),
        notes_dir=data.get("notes_dir", "research/videos"),
        language=data.get("language", "ko"),
        propose_guidelines=propose.get("guidelines", "").strip(),
    )
