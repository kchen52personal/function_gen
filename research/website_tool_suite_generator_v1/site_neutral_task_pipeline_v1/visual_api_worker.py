"""One-shot API worker; credentials arrive through stdin, never command line."""
import json
import sys
from factory import ENDPOINT, _post_json


def main():
    try:
        request=json.load(sys.stdin)
        response=_post_json(ENDPOINT,request['key'],request['payload'],request['timeout'])
        print(json.dumps({'ok':True,'response':response},ensure_ascii=False))
    except Exception as exc:
        # Class names and HTTP status only: never expose exception bodies, headers or keys.
        cause = exc.__cause__
        status = getattr(cause, 'code', None)
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,
                          'cause_type':type(cause).__name__ if cause else None,
                          'http_status':status if type(status) is int and 100 <= status <= 599 else None}))

if __name__=='__main__': main()
