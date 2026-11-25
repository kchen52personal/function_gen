"""Conservative diagnostics for fixture proxy traces; never proves completeness."""
import hashlib
import hmac

GAPS={'uninspected_frame','uninspected_server_frame','unparsed_text_frame',
      'action_capture_failed','action_capture_skipped','action_capture_invalid',
      'trace_parse_failure','trace_overflow','reserved_candidate_id','relay_error'}
NON_MOUSE_ACTIONS={'Input.insertText','Input.dispatchKeyEvent','Input.dispatchTouchEvent',
                   'Input.synthesizeTapGesture','Input.dispatchDragEvent','Input.imeSetComposition'}


def reflect_trace_status(state,*,action_capture_requested):
    """Never leave a fixture evidence run labeled complete when its trace is incomplete."""
    quality=state.get('trace_quality')
    if (action_capture_requested and state.get('status')=='complete' and
        (not isinstance(quality,dict) or quality.get('status')!='observed_not_proven')):
        state.update(status='incomplete_evidence',reason='trace quality is incomplete')
    return state


def assess_trace(events,*,network_isolated,action_capture_requested,require_forwarding_marker=False):
    result={'status':'incomplete','eligible_for_pipeline_gate':False,
            'limitations':['A clean trace is not proof of correct actions, genuine page state, '
                           'or complete coverage of all browser and network activity.']}
    if not isinstance(events,list) or any(not isinstance(x,dict) or not isinstance(x.get('event'),str) for x in events):
        result['reasons']=['invalid trace'];return result
    reasons=[]
    if not network_isolated:reasons.append('candidate had a direct-network bypass')
    connections=sum(x.get('event')=='websocket_open' for x in events)
    if connections==0:reasons.append('no observed browser connection')
    elif connections>1:reasons.append('multiple browser connections cannot be fully ordered')
    gaps=sorted({x.get('event') for x in events if x.get('event') in GAPS})
    if gaps:reasons.append('uninspected or failed trace events: '+', '.join(gaps))
    abnormal=[x for x in events if x.get('event') in {'client_stream_end','server_stream_end'}
              and x.get('reason') not in {'IncompleteReadError','CancelledError'}]
    if abnormal:reasons.append('abnormal trace stream termination')
    if action_capture_requested and any(x.get('event')=='cdp_method' and
            ((isinstance(x.get('method'),str) and x['method'] in NON_MOUSE_ACTIONS) or
             (x.get('method')=='Input.dispatchMouseEvent' and x.get('mouse_type')=='mouseWheel')) for x in events):
        reasons.append('non-mouse-press input was not captured at action time')
    presses=sum(x.get('event')=='cdp_method' and x.get('method')=='Input.dispatchMouseEvent'
                and x.get('mouse_type')=='mousePressed' for x in events)
    captures=sum(x.get('event')=='action_capture' for x in events)
    forwarded=sum(x.get('event')=='mouse_press_forwarded' for x in events)
    if action_capture_requested and (presses==0 or captures!=presses):
        reasons.append('requested mouse-press captures are missing or do not match observed presses')
    if action_capture_requested and presses==captures and presses:
        pending=[];bad_order=False
        for entry in events:
            if (entry.get('event')=='cdp_method' and entry.get('method')=='Input.dispatchMouseEvent'
                and entry.get('mouse_type')=='mousePressed'):
                if type(entry.get('x')) not in (int,float) or type(entry.get('y')) not in (int,float):
                    bad_order=True
                else:pending.append((entry['x'],entry['y']))
            elif entry.get('event')=='action_capture':
                if not pending or pending.pop(0)!=(entry.get('x'),entry.get('y')):bad_order=True
        if pending or bad_order:reasons.append('mouse-press and capture order or coordinates do not match')
    if require_forwarding_marker and presses!=forwarded:
        reasons.append('mouse press and forwarding markers do not match')
    elif require_forwarding_marker:
        active=[];bad_forward=False
        for entry in events:
            if (entry.get('event')=='cdp_method' and entry.get('method')=='Input.dispatchMouseEvent'
                and entry.get('mouse_type')=='mousePressed'):
                active.append({'xy':(entry.get('x'),entry.get('y')),'captured':False})
            elif entry.get('event')=='action_capture':
                if active and active[0]['xy']==(entry.get('x'),entry.get('y')):active[0]['captured']=True
            elif entry.get('event')=='mouse_press_forwarded':
                if not active:bad_forward=True;continue
                first=active.pop(0)
                if first['xy']!=(entry.get('x'),entry.get('y')) or (action_capture_requested and not first['captured']):
                    bad_forward=True
        if active or bad_forward:reasons.append('mouse forwarding order or coordinates do not match')
    result.update(status='incomplete' if reasons else 'observed_not_proven',reasons=reasons,
                  mouse_presses=presses,action_captures=captures,mouse_presses_forwarded=forwarded)
    return result


def check_action_image_digests(events,images):
    """Match recorded action-image hashes; does not establish image provenance."""
    result={'status':'incomplete','eligible_for_pipeline_gate':False}
    if not isinstance(events,list) or not isinstance(images,list):return result
    captures=[x for x in events if isinstance(x,dict) and x.get('event')=='action_capture']
    if not captures or len(captures)!=len(images):return result
    for item,data in zip(captures,images):
        digest=item.get('sha256')
        if (not isinstance(data,bytes) or not isinstance(digest,str) or len(digest)!=64 or not digest.isascii() or
            type(item.get('bytes')) is not int or item['bytes']!=len(data) or
            not hmac.compare_digest(digest,hashlib.sha256(data).hexdigest())):
            return result
    result.update(status='matched_not_proven',count=len(images))
    return result


def measure_forwarding_delays(events):
    """Measure proxy receive-to-forward duration, not end-to-end task latency."""
    result={'status':'incomplete','delays_ms':[],
            'limitation':'Includes proxy processing; excludes browser completion and total task time.'}
    if not isinstance(events,list) or any(not isinstance(x,dict) for x in events):return result
    pending=[];durations=[]
    for entry in events:
        is_press=(entry.get('event')=='cdp_method' and entry.get('method')=='Input.dispatchMouseEvent'
                  and entry.get('mouse_type')=='mousePressed')
        if is_press:
            if type(entry.get('monotonic_ns')) is not int:return result
            pending.append(entry)
        elif entry.get('event')=='mouse_press_forwarded':
            if not pending or type(entry.get('monotonic_ns')) is not int:return result
            before=pending.pop(0)
            if (before.get('x'),before.get('y'))!=(entry.get('x'),entry.get('y')):return result
            duration=entry['monotonic_ns']-before['monotonic_ns']
            if duration<0:return result
            durations.append(round(duration/1_000_000,3))
    if not pending and durations:result.update(status='measured',delays_ms=durations)
    return result
