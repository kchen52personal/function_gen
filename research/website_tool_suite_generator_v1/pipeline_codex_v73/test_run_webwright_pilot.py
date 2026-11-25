from pathlib import Path

import pytest

from run_webwright_pilot import embed_seed_text


def test_embed_seed_text_includes_bounded_file(tmp_path: Path) -> None:
    evidence = tmp_path / "prior_evidence"
    evidence.mkdir()
    (evidence / "error.txt").write_text("observed failure", encoding="utf-8")
    result = embed_seed_text("repair", tmp_path, ["error.txt"])
    assert "observed failure" in result
    assert "PRESERVED EVIDENCE: error.txt" in result


def test_embed_seed_text_rejects_parent_escape(tmp_path: Path) -> None:
    (tmp_path / "prior_evidence").mkdir()
    with pytest.raises(ValueError, match="safe relative"):
        embed_seed_text("repair", tmp_path, ["../secret"])
