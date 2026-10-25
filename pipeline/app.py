#!/usr/bin/env python3
"""The "Update website" control panel: a small page on this computer (http://127.0.0.1:8777)
with buttons to check the data, preview the site, make coding worksheets and publish.
Started by the "Update website" launcher; it stops when you close the terminal window."""
import json, mimetypes, os, subprocess, sys, threading, tomllib, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
PORT = int(os.environ.get('IL_AGENDAS_PORT', 8777))
CFG = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
OUT = ROOT / CFG['build']['output']
VERSION = subprocess.run(['git', 'log', '-1', '--format=%h, %cd', '--date=format:%b %d %Y %H:%M'], cwd=ROOT, capture_output=True, text=True).stdout.strip() or 'unknown'
JOB = {'name': None, 'running': False, 'log': [], 'code': None}
LOCK = threading.Lock()


def run(name, args, env=None):
    with LOCK:
        if JOB['running']:
            return False
        JOB.update(name=name, running=True, log=[], code=None)

    def work():
        p = subprocess.Popen([sys.executable, '-u', *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, encoding='utf-8', errors='replace', env={**os.environ, **(env or {})})
        for line in p.stdout:
            JOB['log'].append(line.rstrip())
        JOB['code'] = p.wait()
        JOB['running'] = False
    threading.Thread(target=work, daemon=True).start()
    return True


def status():
    st = OUT / 'status.json'
    s = json.loads(st.read_text()) if st.exists() else None
    pub = DATA / 'state' / 'publish_log.csv'
    last_pub = pub.read_text().strip().splitlines()[-1].split(',')[0] if pub.exists() and len(pub.read_text().strip().splitlines()) > 1 else None
    return {'version': VERSION, 'build': s, 'published': last_pub, 'method': CFG['publish'].get('method'), 'sessions': [int(x['ga']) for x in CFG['bills']['sessions']],
            'job': {k: JOB[k] for k in ('name', 'running', 'code')}, 'log': JOB['log'][-400:]}


PAGE = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Update website</title>
<style>
:root{--ink:#1b2330;--ink2:#4b5566;--muted:#7b8494;--line:#e4e9f0;--sunk:#f4f7fb;--accent:#2c67ad;--btn:#dcebfb;--btnink:#1d4f8a;--ok:#e2f5ec;--bad:#fde8e6}
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:#fff;max-width:880px;margin:0 auto;padding:28px 16px}
h1{font-size:24px;margin:0 0 4px} p{margin:0} .muted{color:var(--muted)}
.status{margin:20px 0;padding:14px 16px;border-radius:8px;background:var(--sunk)} .status.ok{background:var(--ok)} .status.bad{background:var(--bad)}
.status b{display:block;font-size:17px}
.steps{display:grid;gap:12px}
.step{border:1px solid var(--line);border-radius:8px;padding:14px 16px;display:grid;grid-template-columns:28px 1fr auto;gap:12px;align-items:center}
.step .n{width:26px;height:26px;border-radius:50%;background:var(--sunk);display:grid;place-items:center;font-weight:600;color:var(--ink2)}
.step h2{font-size:16px;margin:0} .step p{font-size:13.5px;color:var(--ink2)}
button,a.btn{font:inherit;border:1px solid #cfd7e2;background:#fff;border-radius:6px;padding:8px 14px;cursor:pointer;text-decoration:none;color:var(--ink);white-space:nowrap}
button.primary{background:var(--btn);color:var(--btnink);border-color:transparent;font-weight:600}
button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
select,input{font:inherit;padding:7px 9px;border:1px solid #cfd7e2;border-radius:6px}
pre{background:#0f1720;color:#dbe4ee;border-radius:8px;padding:12px;font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;max-height:320px;overflow:auto;white-space:pre-wrap;margin-top:16px}
@media (max-width:640px){.step{grid-template-columns:28px 1fr}.step .act{grid-column:2}}
</style>
<h1>Update the Illinois Policy Agendas website</h1>
<p class="muted">Coders save into the <code>inputs</code> folder in Dropbox. Then check, look, and publish. The site's code updates itself from GitHub each time this panel opens.</p>
<div class="status" id="status">Loading…</div>
<p class="muted" id="version" style="font-size:12.5px;margin:-12px 0 16px"></p>
<div class="steps">
  <div class="step"><span class="n">1</span><div><h2>Check data</h2><p>Reads every input file, checks the codes, and rebuilds the site on this computer. Takes about a minute.</p></div>
    <div class="act"><button class="primary" id="build">Check data</button></div></div>
  <div class="step"><span class="n">2</span><div><h2>Look it over</h2><p>The report lists errors (which block publishing), warnings and what changed since the last publish. The preview is the site exactly as it will appear.</p></div>
    <div class="act row"><a class="btn" href="/report" target="_blank">Open report</a><a class="btn" href="/preview/" target="_blank">Preview site</a></div></div>
  <div class="step"><span class="n">3</span><div><h2>Publish</h2><p id="pubdesc"></p></div>
    <div class="act row"><input type="password" id="pw" placeholder="Server password" hidden><button class="primary" id="publish">Publish</button></div></div>
  <div class="step"><span class="n">+</span><div><h2>Think tank logos</h2><p>Visits each think tank's website (listed in <code>profiles.toml</code>) and saves its logo into <code>content/thinktanks/logos</code>. Review them before publishing, and use them with each organization's permission.</p></div>
    <div class="act row"><button id="logos">Fetch logos</button><a class="btn" href="/logos" target="_blank">Review logos</a></div></div>
  <div class="step"><span class="n">+</span><div><h2>Legislators</h2><p>Reads the House and Senate member lists on ilga.gov and saves each member's party, district and official photo into <code>content/legislators</code>. Run it at the start of each session or when members change, then review and publish.</p></div>
    <div class="act row"><button id="legs">Fetch legislators</button><a class="btn" href="/legislators" target="_blank">Review legislators</a></div></div>
  <div class="step"><span class="n">+</span><div><h2>Coding worksheet</h2><p>Makes a spreadsheet of every bill not yet coded in a session, in <code>inputs/worksheets</code>. Fill in <code>subtopic</code> and save it into that session's codes folder.</p></div>
    <div class="act row"><select id="ga"></select><button id="sheet">Make worksheet</button></div></div>
</div>
<pre id="log" hidden></pre>
<script>
const $ = s => document.querySelector(s);
const desc = {github:'Copies the site into the GitHub repository (docs/) and pushes it. GitHub Pages shows the new version within a minute or two.', zip:'Makes a dated zip of the site in build/releases to hand to IT or unzip into the web folder.', folder:'Copies the site into the web folder set in config.toml.', sftp:'Uploads the changed files to the web server set in config.toml.'};
let last;
async function poll(){
  const s = await fetch('/status').then(r=>r.json()); last = s;
  const b = s.build, busy = s.job.running;
  $('#status').className = 'status ' + (b ? (b.ok ? 'ok' : 'bad') : '');
  $('#status').innerHTML = b ? `<b>${b.ok ? 'Last check passed: ready to publish' : `Last check found ${b.errors} error${b.errors>1?'s':''}: open the report`}</b>Checked ${b.built}. ${s.published ? 'Last published '+s.published+'.' : 'Not published from this computer yet.'}` : '<b>Not checked yet</b>Start with step 1.';
  if (busy) $('#status').innerHTML = `<b>Working: ${s.job.name}…</b>This page updates by itself.`;
  $('#version').textContent = 'Site code version ' + s.version + ' (updates from GitHub each time you open Update website)';
  $('#pubdesc').textContent = desc[s.method] || '';
  $('#pw').hidden = s.method !== 'sftp';
  for (const id of ['build','sheet','logos','legs']) $('#'+id).disabled = busy;
  $('#publish').disabled = busy || !b || !b.ok;
  if (!$('#ga').options.length) $('#ga').innerHTML = s.sessions.slice().reverse().map(g=>`<option value="${g}">${g}th General Assembly</option>`).join('');
  if (s.log.length){ const L = $('#log'); L.hidden = false; const atEnd = L.scrollTop + L.clientHeight >= L.scrollHeight - 8; L.textContent = s.log.join('\n'); if (atEnd) L.scrollTop = L.scrollHeight; }
  setTimeout(poll, busy ? 700 : 3000);
}
const post = (url, body) => fetch(url, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body||{})}).then(r=>r.json()).then(r=>{ if (!r.ok) alert(r.message); setTimeout(poll, 200); });
$('#build').onclick = () => post('/run/build');
$('#sheet').onclick = () => post('/run/worksheet', {ga: +$('#ga').value});
$('#logos').onclick = () => post('/run/logos');
$('#legs').onclick = () => post('/run/legislators');
$('#publish').onclick = () => post('/run/publish', {password: $('#pw').value || null});
poll();
</script>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype='text/html; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == '/':
            return self.send(200, PAGE)
        if path == '/status':
            return self.send(200, json.dumps(status()), 'application/json')
        if path == '/report':
            f = OUT / 'report.html'
            return self.send(200, f.read_bytes()) if f.exists() else self.send(404, 'Run "Check data" first.')
        if path == '/logos':
            f = OUT / 'logos.html'
            return self.send(200, f.read_bytes()) if f.exists() else self.send(404, 'Run "Fetch logos" first.')
        if path == '/legislators':
            f = OUT / 'legislators.html'
            return self.send(200, f.read_bytes()) if f.exists() else self.send(404, 'Run "Fetch legislators" first.')
        if path.startswith('/content/legislators/photos/'):  # images on the legislator review page
            base = (ROOT / 'content' / 'legislators' / 'photos').resolve()
            f = (ROOT / path.lstrip('/')).resolve()
            if f.parent != base or not f.is_file():
                return self.send(404, 'Not found')
            return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        if path.startswith('/content/thinktanks/logos/'):  # images shown on the logo review page
            base = (ROOT / 'content' / 'thinktanks' / 'logos').resolve()
            f = (ROOT / path.lstrip('/')).resolve()
            if f.parent != base or not f.is_file():
                return self.send(404, 'Not found')
            return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        if path.startswith('/preview'):
            rel = path[len('/preview'):].lstrip('/') or 'index.html'
            site = (OUT / 'site').resolve()
            f = (site / rel).resolve()
            if site not in f.parents and f != site or not f.is_file():
                return self.send(404, 'Not found. Run "Check data" first.')
            return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        self.send(404, 'Not found')

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n) or b'{}')
        path = urlparse(self.path).path
        if path == '/run/build':
            ok = run('checking data', ['pipeline/build.py'])
        elif path == '/run/worksheet':
            ok = run('making a worksheet', ['pipeline/build.py', '--worksheet', str(int(body.get('ga')))])
        elif path == '/run/logos':
            ok = run('fetching think tank logos', ['pipeline/fetch_logos.py'])
        elif path == '/run/legislators':
            ok = run('fetching legislators from ilga.gov', ['pipeline/fetch_legislators.py'])
        elif path == '/run/publish':
            env = {'IL_AGENDAS_SFTP_PASSWORD': body['password']} if body.get('password') else None
            ok = run('publishing', ['pipeline/publish.py'], env)
        else:
            return self.send(404, json.dumps({'ok': False, 'message': 'Unknown action'}), 'application/json')
        self.send(200, json.dumps({'ok': ok, 'message': '' if ok else 'Something is already running. Wait for it to finish.'}), 'application/json')


def main():
    # an older control panel may still be running in another window; take the next free port instead of failing
    for port in range(PORT, PORT + 20):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)  # this computer only
            break
        except OSError:
            print(f'Port {port} is in use (probably an older control panel still open in another window); trying {port + 1}.')
    else:
        raise SystemExit('Could not start the control panel: close the other "Update website" windows and try again.')
    url = f'http://127.0.0.1:{port}/'
    print(f'Control panel: {url}\nLeave this window open while you work; close it when you are done.')
    if not os.environ.get('IL_AGENDAS_NO_BROWSER'):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
