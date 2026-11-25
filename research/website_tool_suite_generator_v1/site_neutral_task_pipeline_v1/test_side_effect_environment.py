import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT/'runtime'))
sys.path.insert(0, str(PROJECT/'pipeline_codex_v73'))
sys.path.insert(0, str(PROJECT/'vendor/Webwright/src'))
from side_effect_environment import StaticSmokeMixin


def test_static_check_never_executes_changed_script(tmp_path):
    marker = tmp_path/'execution-marker'
    script = tmp_path/'final_script.py'
    script.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n')
    class Fake(StaticSmokeMixin):
        def _final_script_path(self): return script
    fake = Fake()
    assert 'was NOT executed' in fake._smoke_changed_script()
    assert not marker.exists()
    assert fake._smoke_changed_script() is None
    script.write_text('not valid python !')
    assert 'STATIC CHECK FAILED' in fake._smoke_changed_script()
    assert not marker.exists()
