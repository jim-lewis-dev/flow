"""Local UI test harness: python3 -m mobile.preview_server --data /tmp/flow-phone.

Only loopback is served. This is a development aid, not part of the APK runtime.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import argparse
import json
from pathlib import Path
from .bridge import request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    parser.add_argument('--port', type=int, default=8768)
    args=parser.parse_args()
    assets=Path(__file__).resolve().parents[1]/'android/app/src/main/assets'
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(assets),**kw)
        def do_POST(self):
            if self.path != '/rpc':self.send_error(404);return
            if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}'):
                self.send_error(403);return
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<4*1024*1024:self.send_error(413);return
            payload=self.rfile.read(length).decode()
            response=request(args.data,payload).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
        def log_message(self,*args):pass
    HTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

if __name__=='__main__':main()
