"""Loopback F1 fixture: synthetic IO only; never imports the product backend.
Run python tests/ux_browser_fixture.py and open http://127.0.0.1:8766/.
Query flags: preview (clean interactive demo), invalid, risk, sftp, nodrives; lang=en/es/pt. Stop with Ctrl+C.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time
from urllib.parse import urlsplit
WEB = Path(__file__).resolve().parents[1] / "web"
FIXTURE = r"""
<script>
const previewErrors=[];
const recordPreviewError=message=>{previewErrors.push(String(message));document.getElementById('fixture-result')?.setAttribute('data-errors',JSON.stringify(previewErrors));};
window.addEventListener('error',event=>recordPreviewError(event.message));
window.addEventListener('unhandledrejection',event=>recordPreviewError(event.reason));
Object.defineProperty(navigator,'language',{value:new URLSearchParams(location.search).get('lang')||'en'});
window.addEventListener('load', () => {
 const params=new URLSearchParams(location.search);
 const preview=params.has('preview');
 const output=document.createElement('output');output.id='fixture-result';output.dataset.errors=JSON.stringify(previewErrors);
 output.style.cssText='position:fixed;bottom:0;right:0;z-index:9999;background:#222;color:white;font-size:11px';
 output.textContent=preview?'Vista previa · datos simulados':'SIMULATED IO — flash calls: 0';document.body.appendChild(output);
 let calls=0, lists=[], downloads=[];
 window.pywebview={token:'fixture-only',api:{
  start_flash:(...args)=>{output.dataset.arguments=JSON.stringify(args);output.textContent=`SIMULATED IO — flash calls: ${++calls}`;return Promise.resolve(true);},
  get_drives:()=>Promise.resolve(params.has('nodrives')?[]:[{id:99,name:'SIMULATED SD',size:'32 GB',high_risk:false}]),
  browse_image:()=>Promise.resolve('C:/preview/raspios.img'),
  set_preferences:()=>Promise.resolve(true),resize_ssh_pty:()=>{},scan_network:()=>Promise.resolve([]),
  download_file:(...args)=>{if(preview)return Promise.resolve(true);output.dataset.download=JSON.stringify(args);return new Promise(resolve=>downloads.push(resolve));}
 }};
 window.fetch=url=>preview?Promise.resolve({ok:true,json:async()=>({path:new URL(url,location.origin).searchParams.get('path'),generation:7,items:[{name:'configs',is_dir:true},{name:'logs',is_dir:true},{name:'printer.cfg',is_dir:false},{name:'macros.cfg',is_dir:false},{name:'moonraker.conf',is_dir:false}]})}):new Promise((resolve,reject)=>lists.push({url,resolve,reject}));
 const toolbar=document.createElement('div');toolbar.id='fixture-controls';
 toolbar.style.cssText='position:fixed;top:0;right:0;z-index:9999;display:flex;gap:3px;background:#222';document.body.appendChild(toolbar);toolbar.hidden=preview;
 const button=(text,fn)=>{const el=document.createElement('button');el.textContent=text;el.type='button';el.addEventListener('click',fn);toolbar.appendChild(el);};
 button('Fixture: list OK',()=>{const req=lists.pop();if(req)req.resolve({ok:true,json:async()=>({path:new URL(req.url,location.origin).searchParams.get('path'),generation:7,items:[{name:'configs',is_dir:true},{name:'printer.cfg',is_dir:false},{name:'very-long-<name>-'.repeat(9)+'.cfg',is_dir:false}]})});});
 button('Fixture: list error',()=>{const req=lists.pop();if(req)req.reject(new Error('fixture'));});
 button('Fixture: disconnect',()=>{sshConnected=false;refreshSftpBrowser();});
 button('Fixture: download OK',()=>downloads.splice(0).forEach(resolve=>resolve(true)));
 button('Fixture: success',()=>showStudioModal('success-modal','success-done-btn'));
 button('Fixture: scan start',()=>startFirstBootDiscovery());
 const drive=document.getElementById('drive-select');drive.replaceChildren(new Option('SIMULATED SD — no physical device','99'));
 driveIdentitySnapshots.set('99',{number:99,high_risk:params.has('risk'),fixture:true});
 const fields={'hostname-input':'kace.local','ssh-username':'kace','ssh-password':params.has('invalid')?'':'fixture-password','ssh-password-confirm':params.has('invalid')?'':'fixture-password','wifi-ssid':'','wifi-password':'','wifi-password-confirm':'','image-source-select':'raspios_lite'};
 if(preview){Object.keys(fields).forEach(id=>fields[id]=document.getElementById(id).defaultValue ?? document.getElementById(id).value);}
 Object.entries(fields).forEach(([id,value])=>{const field=document.getElementById(id);field.value=value;if(field.tagName==='SELECT')field.dispatchEvent(new Event('presentationchange'));});toggleImageSource(fields['image-source-select']);
 if(params.has('nodrives')){drive.replaceChildren(new Option('No removable drives found.',''));driveIdentitySnapshots.clear();}
 if(preview||params.has('sftp')){sshConnected=true;refreshSftpBrowser();}
 if(params.has('sftp'))switchTab('terminal-tab');
});
</script>
"""
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(WEB),**kwargs)
 def do_GET(self):
  path=urlsplit(self.path).path
  if path in ('/','/index.html'):
   payload=(WEB/'index.html').read_text(encoding='utf8').replace('</head>',FIXTURE+'</head>').replace('?v=2.2.4', '?ux-fixture='+str(time.time_ns())).encode('utf8')
   self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
  elif path.startswith('/api/'):self.send_error(503,'Fixture has no backend')
  else:super().do_GET()
if __name__=='__main__':
 print('Synthetic UI fixture http://127.0.0.1:8766',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
