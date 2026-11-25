import importlib.util
import json
from pathlib import Path
import sys
import types
import pytest
from private_session import session_file
from public_environment import IsolatedPublicEnvironment


def private_fixture(tmp_path):
    root=tmp_path/'sessions'; root.mkdir()
    directory=root/'one'; directory.mkdir(mode=0o700)
    (directory/'metadata.json').write_text(json.dumps({'website':'https://example.org/',
                                                     'login_cookie_received':True}))
    state=directory/'storage_state.json'
    state.write_text(json.dumps({'cookies':[],'origins':[]})); state.chmod(0o600)
    return root,directory,state


def test_private_session_matches_site_and_permissions(tmp_path):
    root,directory,state=private_fixture(tmp_path)
    assert session_file(directory,'https://example.org/',root=root)==state.resolve()
    with pytest.raises(ValueError):session_file(directory,'https://other.example/',root=root)
    state.chmod(0o644)
    with pytest.raises(ValueError):session_file(directory,'https://example.org/',root=root)


def test_private_session_rejects_symlinks_and_missing_login(tmp_path):
    root,directory,state=private_fixture(tmp_path)
    alias=root/'alias'; alias.symlink_to(directory,target_is_directory=True)
    with pytest.raises(ValueError):session_file(alias,'https://example.org/',root=root)
    (directory/'metadata.json').write_text(json.dumps({'website':'https://example.org/',
                                                     'login_cookie_received':False}))
    with pytest.raises(ValueError):session_file(directory,'https://example.org/',root=root)


def test_non_authenticated_run_clears_inherited_private_session(monkeypatch,tmp_path):
    monkeypatch.setenv('WW_PRIVATE_SESSION_FILE','should-not-inherit')
    monkeypatch.setenv('WW_PUBLIC_LOGIN_MODE','1')
    monkeypatch.setenv('WW_BROWSER_SESSION_STATE','should-not-inherit')
    env=IsolatedPublicEnvironment(tmp_path,'https://example.org/').command_environment(tmp_path)
    assert not any(k in env for k in ('WW_PRIVATE_SESSION_FILE','WW_PUBLIC_LOGIN_MODE',
                                      'WW_BROWSER_SESSION_STATE'))


def test_authentication_only_added_by_host_selection(monkeypatch,tmp_path):
    import public_environment
    monkeypatch.setattr(public_environment,'session_file',lambda directory,url:Path('/private/state.json'))
    adapter=IsolatedPublicEnvironment(tmp_path,'https://example.org/',session_directory='approved')
    env=adapter.command_environment(tmp_path)
    assert env['WW_PRIVATE_SESSION_FILE']=='/private/state.json'
    assert env['WW_SESSION_WEBSITE']=='https://example.org/'
    assert not (tmp_path/'state.json').exists()


@pytest.fixture
def policy(monkeypatch):
    # Load the actual adapter with minimal browser classes, without launching one.
    for name in ('playwright.async_api','playwright.sync_api'):
        module=types.ModuleType(name)
        module.BrowserType=type('BrowserType',(),{'launch':lambda *a,**k:None,
                                                'launch_persistent_context':lambda *a,**k:None})
        module.Browser=type('Browser',(),{'new_context':lambda *a,**k:None,
                                        'new_page':lambda *a,**k:None})
        monkeypatch.setitem(sys.modules,name,module)
    path=Path(__file__).parent/'runtime/public_browser_policy.py'
    spec=importlib.util.spec_from_file_location('test_private_browser_policy',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_context_baseline_is_reloaded_without_mutating_original(policy,monkeypatch):
    state={'cookies':[{'name':'example','value':'test-only'}],'origins':[]}
    monkeypatch.setattr(policy,'private_state',lambda:state)
    options=policy.context_options({'storage_state':'untrusted-override'})
    assert options['storage_state']==state
    options['storage_state']['cookies'].clear()
    assert len(state['cookies'])==1
    assert 'localStorage' in policy.local_storage_script({'origins':[]})


def test_policy_fails_closed_on_unexpected_state_path(policy,monkeypatch):
    monkeypatch.setenv('WW_BROWSER_SESSION_STATE','/some/workspace/session.json')
    with pytest.raises(RuntimeError):policy.private_state()
    monkeypatch.delenv('WW_BROWSER_SESSION_STATE')
    assert policy.context_options({'viewport':{'width':1000}})=={'viewport':{'width':1000}}
