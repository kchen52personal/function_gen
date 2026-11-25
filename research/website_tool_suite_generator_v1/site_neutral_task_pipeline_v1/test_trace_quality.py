import pytest
import hashlib
from trace_quality import assess_trace,check_action_image_digests,measure_forwarding_delays,reflect_trace_status


def event(name,**fields):return {'event':name,**fields}


def clean():
    return [event('websocket_open'),event('cdp_method',method='Input.dispatchMouseEvent',mouse_type='mousePressed',x=10,y=20),
            event('action_capture',x=10,y=20),event('client_stream_end',reason='IncompleteReadError')]


def test_clean_trace_is_observed_not_proven_and_never_gateable():
    result=assess_trace(clean(),network_isolated=True,action_capture_requested=True)
    assert result['status']=='observed_not_proven' and not result['eligible_for_pipeline_gate']


@pytest.mark.parametrize('events,isolated,capture',[
    ([],True,True),(clean(),False,True),(clean()[:-2],True,True),
    (clean()+[event('uninspected_frame')],True,True),
    (clean()+[event('unparsed_text_frame')],True,True),
    (clean()+[event('cdp_method',method='Input.insertText')],True,True),
    (clean()+[event('cdp_method',method='Input.dispatchMouseEvent',mouse_type='mouseWheel')],True,True),
    (clean()+[event('websocket_open')],True,True),
    ([event('websocket_open'),event('action_capture',x=10,y=20),
      event('cdp_method',method='Input.dispatchMouseEvent',mouse_type='mousePressed',x=10,y=20)],True,True),
    ([event('websocket_open'),event('cdp_method',method='Input.dispatchMouseEvent',mouse_type='mousePressed',x=10,y=20),
      event('action_capture',x=11,y=20)],True,True),
    (clean()+[event('trace_overflow')],True,True),
    (clean()+[event('server_stream_end',reason='ValueError')],True,True),
    ([event('websocket_open')],True,True)])
def test_unobservable_or_incomplete_traces_do_not_look_clean(events,isolated,capture):
    assert assess_trace(events,network_isolated=isolated,action_capture_requested=capture)['status']=='incomplete'


def test_action_images_must_match_trace_hashes_and_count():
    image=b'fixture'; item=event('action_capture',sha256=hashlib.sha256(image).hexdigest(),bytes=len(image))
    result=check_action_image_digests([item],[image])
    assert result['status']=='matched_not_proven' and not result['eligible_for_pipeline_gate']
    assert check_action_image_digests([item],[b'changed'])['status']=='incomplete'
    assert check_action_image_digests([item],[])['status']=='incomplete'
    assert check_action_image_digests([],[])['status']=='incomplete'


def test_forwarding_marker_can_be_required():
    assert assess_trace(clean(),network_isolated=True,action_capture_requested=True,
                        require_forwarding_marker=True)['status']=='incomplete'
    trace=clean();trace.insert(3,event('mouse_press_forwarded',x=10,y=20))
    assert assess_trace(trace,network_isolated=True,action_capture_requested=True,
                        require_forwarding_marker=True)['status']=='observed_not_proven'
    trace[3]['x']=11
    assert assess_trace(trace,network_isolated=True,action_capture_requested=True,
                        require_forwarding_marker=True)['status']=='incomplete'


def test_forwarding_delay_does_not_claim_end_to_end_latency():
    trace=[event('cdp_method',method='Input.dispatchMouseEvent',mouse_type='mousePressed',x=1,y=2,monotonic_ns=100),
           event('mouse_press_forwarded',x=1,y=2,monotonic_ns=2_000_100)]
    result=measure_forwarding_delays(trace)
    assert result['status']=='measured' and result['delays_ms']==[2.0]
    trace[1]['monotonic_ns']=0
    assert measure_forwarding_delays(trace)['status']=='incomplete'


def test_incomplete_or_missing_trace_overrides_complete_fixture_status():
    for quality in ({'status':'incomplete'},None):
        state={'status':'complete','trace_quality':quality}
        assert reflect_trace_status(state,action_capture_requested=True)['status']=='incomplete_evidence'
    state={'status':'complete','trace_quality':{'status':'observed_not_proven'}}
    assert reflect_trace_status(state,action_capture_requested=True)['status']=='complete'
