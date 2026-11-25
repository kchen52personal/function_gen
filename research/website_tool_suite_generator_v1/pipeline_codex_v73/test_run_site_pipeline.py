from pathlib import Path

import pytest

from run_site_pipeline import _inside_shared


def test_run_root_must_stay_inside_shared_hans() -> None:
    with pytest.raises(ValueError):
        _inside_shared(Path("/tmp/outside-project"))


def test_shared_hans_run_root_is_allowed() -> None:
    path = Path("/shared/hans/research/example-run")
    assert _inside_shared(path) == path.resolve()
