from runtime.access_guard import repeated_access_errors


def test_counts_only_distinct_target_host_error_steps(tmp_path):
    logs=tmp_path/'logs';logs.mkdir()
    (logs/'step_0001.log').write_text('net::ERR_CONNECTION_REFUSED https://example.org/\n'*10)
    (logs/'step_0002.log').write_text('NS_ERROR_CONNECTION_REFUSED https://example.org/search')
    (logs/'step_0003.log').write_text('net::ERR_CONNECTION_REFUSED https://another.example/')
    result=repeated_access_errors(tmp_path,'https://example.org/')
    assert result=={'detected':False,'distinct_failed_steps':2}
    (logs/'step_0004.log').write_text('net::ERR_CONNECTION_REFUSED https://example.org/')
    result=repeated_access_errors(tmp_path,'https://example.org/')
    assert result=={'detected':True,'distinct_failed_steps':3}


def test_ignores_symlinked_logs(tmp_path):
    logs=tmp_path/'logs';logs.mkdir()
    outside=tmp_path/'other';outside.write_text('net::ERR_CONNECTION_REFUSED https://example.org/')
    (logs/'step_0001.log').symlink_to(outside)
    assert repeated_access_errors(tmp_path,'https://example.org/',minimum=1)['detected'] is False
