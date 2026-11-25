import json
import pytest
import evidence_capsule as c

KEY=b'K'*32
SCRIPT=b'print("candidate")\n'
TASK={'task_id':'test','request':'open an item'}
PNG=b'\x89PNG\r\n\x1a\nexample'


def make(tmp_path):
    path=tmp_path/'capsule'
    c.create_capsule(path,run_id='run_001',task=TASK,script=SCRIPT,
                     captures=[{'role':'before','data':PNG},{'role':'after','data':PNG+b'2'}],key=KEY)
    return path


def check(path,**overrides):
    args={'run_id':'run_001','task':TASK,'script':SCRIPT,'key':KEY}
    args.update(overrides)
    return c.verify_capsule(path,**args)


def test_matching_content_is_integrity_only(tmp_path):
    report=check(make(tmp_path))
    assert report['integrity']=='valid'
    assert report['eligible_for_pipeline_gate'] is False and report['is_ground_truth'] is False


@pytest.mark.parametrize('changes',[
    {'run_id':'different'}, {'task':{'task_id':'different'}},
    {'script':SCRIPT+b'# changed'}, {'key':b'X'*32}, {'key':b'short'},
])
def test_changed_run_task_script_or_key_fails(tmp_path,changes):
    assert check(make(tmp_path),**changes)['integrity']=='invalid'


def test_modified_capture_is_detected(tmp_path):
    path=make(tmp_path)
    (path/'capture_01.png').write_bytes(PNG+b'forged')
    assert check(path)['integrity']=='invalid'


def test_modified_manifest_is_detected(tmp_path):
    path=make(tmp_path); manifest=path/'manifest.json'
    data=json.loads(manifest.read_text());data['body']['captures'][1]['role']='observation'
    manifest.write_text(json.dumps(data))
    assert check(path)['integrity']=='invalid'


def test_trace_metadata_is_bound_and_expected_value_is_checked(tmp_path):
    path=tmp_path/'with-trace';metadata={'trace_sha256':'a'*64,'scope':'prefix'}
    c.create_capsule(path,run_id='run_001',task=TASK,script=SCRIPT,
                     captures=[{'role':'action','data':PNG}],key=KEY,evidence_metadata=metadata)
    assert check(path,expected_metadata=metadata)['integrity']=='valid'
    assert check(path,expected_metadata={'trace_sha256':'b'*64})['integrity']=='invalid'
    manifest=path/'manifest.json';body=json.loads(manifest.read_text())
    body['body']['evidence_metadata']['trace_sha256']='b'*64
    manifest.write_text(json.dumps(body))
    assert check(path)['integrity']=='invalid'


def test_missing_or_symlinked_capture_fails(tmp_path):
    path=make(tmp_path); image=path/'capture_01.png'
    image.unlink()
    assert check(path)['integrity']=='invalid'
    image.symlink_to(path/'capture_00.png')
    assert check(path)['integrity']=='invalid'


def test_symlinked_root_fails(tmp_path):
    path=make(tmp_path); alias=tmp_path/'alias';alias.symlink_to(path,target_is_directory=True)
    assert check(alias)['integrity']=='invalid'


def test_invalid_input_does_not_create_capsule(tmp_path):
    path=tmp_path/'no-capsule'
    with pytest.raises(ValueError):
        c.create_capsule(path,run_id='run',task=TASK,script=SCRIPT,
                         captures=[{'role':'after','data':b'not a png'}],key=KEY)
    assert not path.exists()


def test_existing_capsule_not_overwritten(tmp_path):
    path=make(tmp_path); prior=(path/'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        c.create_capsule(path,run_id='run',task=TASK,script=SCRIPT,
                         captures=[{'role':'after','data':PNG}],key=KEY)
    assert (path/'manifest.json').read_bytes()==prior


def test_multiple_action_images_are_all_bound(tmp_path):
    path=tmp_path/'multi'
    images=[{'role':'before','data':PNG},{'role':'after','data':PNG+b'a'},
            {'role':'action','data':PNG+b'1'},{'role':'action','data':PNG+b'2'}]
    body=c.create_capsule(path,run_id='run_001',task=TASK,script=SCRIPT,captures=images,key=KEY)
    assert len(body['captures'])==4 and check(path)['integrity']=='valid'
    (path/'capture_03.png').write_bytes(PNG+b'tampered')
    assert check(path)['integrity']=='invalid'
