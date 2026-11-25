from evidence_batch_planner import plan_batches


def test_multi_action_batches_preserve_all_checks_and_exact_images():
    evidence=[{'key':'before','role':'before'},{'key':'after','role':'after'}]+[
        {'key':f'action{i}','role':'action'} for i in range(3)]
    checks=[{'id':'R1','requirement':'First action','evidence':['before','action0','after']},
            {'id':'R2','requirement':'Second action','evidence':['action1','after']},
            {'id':'R3','requirement':'Third action','evidence':['action2','after']}]
    plan=plan_batches(evidence,checks)
    assert plan['status']=='ready' and not plan['eligible_for_pipeline_gate']
    assert len(plan['batches'])==2 and all(len(b['evidence_keys'])<=3 for b in plan['batches'])
    mapped={c['id']:(b,c) for b in plan['batches'] for c in b['checks']}
    assert set(mapped)=={'R1','R2','R3'}
    for original in checks:
        batch,item=mapped[original['id']]
        reconstructed=[batch['evidence_keys'][int(image.split('_')[1])-1] for image in item['images']]
        assert reconstructed==original['evidence']


def test_unschedulable_and_unreferenced_evidence_fail_closed():
    evidence=[{'key':f'e{i}','role':'action'} for i in range(4)]
    check={'id':'R1','requirement':'Need four','evidence':[f'e{i}' for i in range(4)]}
    plan=plan_batches(evidence,[check])
    assert plan['status']=='inconclusive' and not plan['batches']
    check['evidence']=['e0']
    plan=plan_batches(evidence,[check])
    assert plan['status']=='inconclusive' and not plan['batches'] and len(plan['unreferenced_evidence'])==3


def test_duplicate_or_unknown_check_evidence_is_rejected():
    evidence=[{'key':'a','role':'action'}]
    check={'id':'R1','requirement':'Check','evidence':['a']}
    assert plan_batches(evidence,[check,check])['status']=='inconclusive'
    assert plan_batches(evidence,[{**check,'evidence':['missing']}])['status']=='inconclusive'
