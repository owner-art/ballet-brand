"""API 호출 없이 전체 파이프라인을 검증한다 (Claude 응답은 가짜로 대체)."""

import json
import shutil
import subprocess

import pytest

from video_insight import analyze as analyze_mod
from video_insight import cli, propose as propose_mod
from video_insight.schemas import Proposal, VideoAnalysis, strict_schema
from video_insight.transcript import parse_vtt, pick_subtitle

FAKE_ANALYSIS = {
    "title": "성인 발레 수업 전 워밍업 루틴",
    "summary": "요약.",
    "format": "브이로그",
    "hook": "첫 장면에서 레그워머를 신는다.",
    "moments": [{"timestamp": "00:01", "what": "레그워머 착용"}],
    "visual_observations": ["검정 니트 워머"],
    "audience_signals": ["30대 직장인 댓글 다수"],
    "claims_and_data": [],
    "lens_findings": [{"question": "무엇을 걸치는가?", "answer": "워머", "evidence": "00:01", "confidence": "high"}],
    "implications": [{"target": "allegro", "action": "레그워머 설명 보강", "rationale": "근거", "priority": "high"}],
    "tags": ["성인발레", "레그워머"],
}


def test_strict_schema_is_flat_and_closed():
    s = strict_schema(VideoAnalysis)
    dumped = json.dumps(s)
    assert "$ref" not in dumped and "$defs" not in dumped
    assert s["properties"]["title"]["type"] == "string"  # "title" 필드는 남아야 한다
    assert set(s["required"]) == set(s["properties"])
    assert s["additionalProperties"] is False
    assert s["properties"]["moments"]["items"]["additionalProperties"] is False
    assert strict_schema(Proposal)["properties"]["edits"]["items"]["required"]


def test_vtt_dedupes_rolling_autosubs(tmp_path):
    vtt = tmp_path / "x.ko.vtt"
    vtt.write_text(
        "WEBVTT\nKind: captions\nLanguage: ko\n\n"
        "00:00:01.000 --> 00:00:02.000\n<c>다리를</c> 데워요\n\n"
        "00:00:02.000 --> 00:00:03.000\n다리를 데워요\n\n"
        "00:01:05.000 --> 00:01:06.000\n워머를 신어요\n",
        encoding="utf-8",
    )
    assert parse_vtt(vtt) == "[00:01] 다리를 데워요\n[01:05] 워머를 신어요"
    en = tmp_path / "x.en.vtt"
    en.write_text("WEBVTT\n")
    assert pick_subtitle([en, vtt]) == vtt


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".video-insight.toml").write_text(
        'name = "테스트"\ncontext_files = ["doc.html"]\nquestions = ["무엇을 걸치는가?"]\n', encoding="utf-8"
    )
    (root / "doc.html").write_text("<p>레그워머는 양말 취급이다.</p>\n<p>기타</p>\n", encoding="utf-8")
    return root


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 없음")
    v = tmp_path / "class.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=duration=6", "-shortest", str(v)],
        check=True,
    )
    return v


def test_analyze_then_propose(project, video, monkeypatch):
    seen = {}

    def fake_ask(*, system, content, schema, model, effort="high"):
        seen[schema.__name__] = content
        if schema is VideoAnalysis:
            return VideoAnalysis.model_validate(FAKE_ANALYSIS)
        return Proposal.model_validate({
            "summary": "레그워머 설명을 기능성 쪽으로 보강.",
            "edits": [
                {"file": "doc.html", "old_string": "레그워머는 양말 취급이다.",
                 "new_string": "레그워머는 양말 취급이지만, 영상에서는 수업 전 필수 준비물이다.",
                 "reason": "영상 근거", "sources": ["local-class"]},
                {"file": "doc.html", "old_string": "없는 문장", "new_string": "x", "reason": "r", "sources": []},
                {"file": "../etc/passwd", "old_string": "a", "new_string": "b", "reason": "r", "sources": []},
            ],
            "not_applied": [],
            "open_questions": ["가격대 확인 필요"],
        })

    monkeypatch.setattr(analyze_mod, "ask", fake_ask)
    monkeypatch.setattr(propose_mod, "ask", fake_ask)

    assert cli.main(["analyze", "-C", str(project), str(video), "--no-whisper", "--frames", "4"]) == 0
    notes = project / "research/videos"
    assert (notes / "local-class.json").exists()
    assert "레그워머 설명 보강" in (notes / "INDEX.md").read_text()
    images = [c for c in seen["VideoAnalysis"] if c["type"] == "image"]
    assert len(images) == 4

    # 두 번째 실행은 건너뛴다
    seen.clear()
    assert cli.main(["analyze", "-C", str(project), str(video), "--no-whisper"]) == 0
    assert "VideoAnalysis" not in seen

    assert cli.main(["propose", "-C", str(project)]) == 0
    doc = (project / "doc.html").read_text()
    assert "필수 준비물" in doc and "<p>기타</p>" in doc
    rec = json.loads((notes / "local-class.json").read_text())
    assert rec["proposed_in"]
    record = next((notes / "proposals").glob("*.md")).read_text()
    assert "반영한 수정 (1)" in record and "적용하지 못한 수정 (2)" in record

    # 반영 후엔 대기 중인 노트가 없다
    seen.clear()
    assert cli.main(["propose", "-C", str(project)]) == 0
    assert "Proposal" not in seen
