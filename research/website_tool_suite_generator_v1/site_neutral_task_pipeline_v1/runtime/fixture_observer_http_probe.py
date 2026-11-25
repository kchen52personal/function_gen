"""Attempt an unsupported DevTools HTTP operation via the fixture relay."""
import json
from urllib.error import HTTPError
from urllib.request import Request,urlopen


def main():
    request=Request('http://127.0.0.1:19223/json/new?http://127.0.0.1:18765/',method='PUT')
    try:
        with urlopen(request,timeout=5) as response: status=response.status
    except HTTPError as exc:
        status=exc.code
    print(json.dumps({'unsupported_http_status':status,'added_cart_item':False}),flush=True)


if __name__=='__main__':main()
