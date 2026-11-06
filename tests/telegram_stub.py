"""Local Telegram substitute for Docker CI; no external messages."""
import json
import time
import threading
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
state = {'methods':{}, 'menu_sent':False, 'query_sent':False, 'widget':None}
lock = threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        params={k:v[0] for k,v in parse_qs(self.rfile.read(int(self.headers.get('Content-Length',0))).decode()).items()}
        method=self.path.rsplit('/',1)[-1]
        if method=='getUpdates': time.sleep(1)
        with lock:
            state['methods'][method]=state['methods'].get(method,0)+1
            result=True
            if method=='sendWidget':
                state['widget']=json.loads(params['chat_data'])
                result={'message_id':77,'chat':{'id':1,'type':'private'}}
            elif method=='editMessageWidgetData':
                state['widget']=json.loads(params['chat_data'])
            elif method=='getUpdates':
                result=[]
                if not state['menu_sent']:
                    state['menu_sent']=True
                    result=[{'update_id':1,'message':{'message_id':1,'chat':{'id':1,'type':'private'},'from':{'id':1},'text':'/menu'}}]
                elif state['widget'] and not state['query_sent'] and 'widget_query' in params.get('allowed_updates',''):
                    state['query_sent']=True
                    result=[{'update_id':2,'widget_query':{'id':'ci-refresh','from':{'id':1},'chat':{'id':1,'type':'private'},'message_id':77,'data':'hb:'+state['widget']['n']+':r'}}]
        self.respond({'ok':True,'result':result})
    def do_GET(self):
        with lock: snapshot=dict(state['methods'])
        self.respond(snapshot)
    def respond(self,payload):
        body=json.dumps(payload).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass
if __name__=='__main__': ThreadingHTTPServer(('0.0.0.0',8765),Handler).serve_forever()
