"""Local Telegram substitute used by Docker CI; no real API calls or messages."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        if self.path.endswith('/getUpdates'): time.sleep(1)
        body=json.dumps({'ok':True,'result':[] if self.path.endswith('/getUpdates') else True}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',8765),Handler).serve_forever()
