#!/usr/bin/env python3
"""The "Update website" control panel: a small page on this computer (http://127.0.0.1:8777)
with buttons to check the data, preview the site, make coding worksheets and publish.
Started by the "Update website" launcher; it stops when you close the terminal window."""
import datetime as dt, json, mimetypes, os, subprocess, sys, threading, tomllib, webbrowser

import qc  # the "Check law codes" page
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
PORT = int(os.environ.get('IL_AGENDAS_PORT', 8777))
CFG = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
OUT = ROOT / CFG['build']['output']
VERSION = subprocess.run(['git', 'log', '-1', '--format=%h, %cd', '--date=format:%b %d %Y %H:%M'], cwd=ROOT, capture_output=True, text=True).stdout.strip() or 'unknown'
JOB = {'name': None, 'running': False, 'log': [], 'code': None, 'notes': []}
LOCK = threading.Lock()


def run(name, args, env=None, steps=None):
    """Run one script, or `steps` [(label, script args, required)] one after another, in the background.
    An optional step that fails is noted and the next one still runs; a required one that fails stops the rest."""
    with LOCK:
        if JOB['running']:
            return False
        JOB.update(name=name, running=True, log=[], code=None, notes=[])

    def one(a):
        p = subprocess.Popen([sys.executable, '-u', *a], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, encoding='utf-8', errors='replace', env={**os.environ, **(env or {})})
        for line in p.stdout:
            JOB['log'].append(line.rstrip())
        return p.wait()

    def work():
        code = 0
        for label, a, required in steps or [(name, args, True)]:
            if steps:
                JOB['name'] = label
                JOB['log'].append(f'\n=== {label} ===')
            code = one(a)
            if code and not required:
                JOB['notes'].append(f'{label}: there were problems (see the log). The rest of the update went ahead.')
                code = 0
            elif code:
                break
        JOB['code'] = code
        JOB['running'] = False
    threading.Thread(target=work, daemon=True).start()
    return True


UPDATE = [('Updating bills from ILGA', ['pipeline/fetch_actions.py'], False),
          ('Looking for new think tank reports', ['pipeline/fetch_reports.py'], False),
          ('Reading the coding sheet', ['pipeline/coding_sheet.py', 'pull'], False),   # these two do nothing without the Google key
          ('Checking data', ['pipeline/build.py'], True),
          ('Adding new bills to the coding sheet', ['pipeline/coding_sheet.py', 'push'], False)]


def when(path):
    return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime('%b %d, %Y %H:%M').replace(' 0', ' ') if path.exists() else None


def status():
    st = OUT / 'status.json'
    s = json.loads(st.read_text()) if st.exists() else None
    pub = DATA / 'state' / 'publish_log.csv'
    last_pub = pub.read_text().strip().splitlines()[-1].split(',')[0] if pub.exists() and len(pub.read_text().strip().splitlines()) > 1 else None
    live = [x for x in CFG['bills']['sessions'] if x.get('status') != 'upcoming']
    latest = max(live, key=lambda x: int(x['ga']))
    todo = (s or {}).get('todo') or {}
    # law-code review progress: the last check's laws against every reviewer's decisions
    review = {}
    for ga, laws in (todo.get('laws') or {}).items():
        last = qc.latest(qc.read_reviews(DATA, int(ga)))
        review[ga] = {'laws': len(laws), 'done': sum(1 for b in laws if b in last),
                      'pending': sum(1 for r in last.values() if r['decision'] == 'recode' and s and r['reviewed_at'] > s['built'])}
    return {'version': VERSION, 'build': s, 'published': last_pub, 'method': CFG['publish'].get('method'), 'sessions': sorted(int(x['ga']) for x in live),
            'ilga': when(DATA / latest.get('actions', 'none') / 'listing.json') if latest.get('actions') else None,
            'reports_file': CFG['thinktanks'].get('new', ''), 'review': review,
            'sheet': f"https://docs.google.com/spreadsheets/d/{CFG['coding_sheet']['id']}/edit" if CFG.get('coding_sheet', {}).get('id') else None,
            'job': {k: JOB[k] for k in ('name', 'running', 'code', 'notes')}, 'log': JOB['log'][-400:]}


PAGE = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Update the IPAP website</title>
<style>
:root{--ink:#1b2330;--ink2:#4b5566;--muted:#7b8494;--line:#e4e9f0;--sunk:#f4f7fb;--accent:#2c67ad;--btn:#13294b;--ok:#e2f5ec;--okink:#1f7a4d;--bad:#fde8e6;--warn:#fff4d6;--warnink:#8a5a00}
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:#fff;max-width:880px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:24px;margin:0 0 4px} h2.sec{font-size:13px;letter-spacing:.04em;color:var(--muted);margin:28px 0 10px;font-weight:700} p{margin:0} .muted{color:var(--muted)}
.status{margin:18px 0 6px;padding:14px 16px;border-radius:10px;background:var(--sunk)} .status.ok{background:var(--ok)} .status.bad{background:var(--bad)} .status b{display:block;font-size:17px}
.status .note{margin-top:6px;color:var(--warnink);font-size:14px}
.steps{display:grid;gap:10px}
.step{border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:grid;grid-template-columns:30px 1fr auto;gap:14px;align-items:center}
.step.main{border-color:#c9d5e6;background:#fbfcfe}
.step .n{width:28px;height:28px;border-radius:50%;background:var(--btn);color:#fff;display:grid;place-items:center;font-weight:700;font-size:14px}
.step h3{font-size:16px;margin:0} .step p{font-size:13.5px;color:var(--ink2)} .step .meta{font-size:12.5px;color:var(--muted);margin-top:3px}
.todo{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.card .big{font-size:28px;font-weight:700;line-height:1} .card .big.done{color:var(--okink)} .card p{font-size:13.5px;color:var(--ink2);flex:1}
.bar{height:6px;background:var(--sunk);border-radius:3px;overflow:hidden} .bar i{display:block;height:100%;background:#5cc493}
button,a.btn{font:inherit;border:1px solid #cfd7e2;background:#fff;border-radius:7px;padding:8px 14px;cursor:pointer;text-decoration:none;color:var(--ink);white-space:nowrap;text-align:center}
button.primary{background:var(--btn);color:#fff;border-color:var(--btn);font-weight:700} button.link{border:0;background:none;color:var(--accent);padding:4px 0;text-decoration:underline}
button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center} .col{display:flex;flex-direction:column;gap:4px;align-items:stretch}
select,input{font:inherit;padding:7px 9px;border:1px solid #cfd7e2;border-radius:7px}
details.more{border:1px solid var(--line);border-radius:10px;padding:4px 16px} details.more summary{cursor:pointer;padding:10px 0;font-weight:600}
.occ{display:grid;gap:0} .occ > div{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:center;padding:12px 0;border-top:1px solid var(--line)}
.occ h3{font-size:15px;margin:0} .occ p{font-size:13px;color:var(--ink2)}
code{font-size:12.5px;background:var(--sunk);padding:1px 4px;border-radius:4px}
pre{background:#0f1720;color:#dbe4ee;border-radius:8px;padding:12px;font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;max-height:320px;overflow:auto;white-space:pre-wrap;margin-top:16px}
@media (max-width:700px){.step{grid-template-columns:30px 1fr}.step .act{grid-column:2}.todo{grid-template-columns:1fr}.occ > div{grid-template-columns:1fr}}
</style>
<h1>Update the IPAP website</h1>
<p class="muted" id="version" style="font-size:12.5px"></p>
<div class="status" id="status">Loading…</div>

<h2 class="sec">PUBLISH AN UPDATE</h2>
<div class="steps">
  <div class="step main"><span class="n">1</span><div><h3>Update and check</h3><p>Gets the latest status of every bill from the General Assembly, looks for new think tank reports, then checks every file and rebuilds the site on this computer. A few minutes (about 40 the first time on a computer).</p><p class="meta" id="ilga"></p></div>
    <div class="act col"><button class="primary" id="update">Update and check</button><button class="link" id="build" title="Skip the downloads: just check the files in Dropbox again">Check only</button></div></div>
  <div class="step"><span class="n">2</span><div><h3>Look it over</h3><p>The report lists errors (which block publishing), warnings and what changed since the last publish. The preview is the site exactly as it will appear.</p></div>
    <div class="act row"><a class="btn" href="/report" target="_blank">Open report</a><a class="btn" href="/preview/" target="_blank">Preview site</a></div></div>
  <div class="step"><span class="n">3</span><div><h3>Publish</h3><p id="pubdesc"></p></div>
    <div class="act row"><input type="password" id="pw" placeholder="Server password" hidden><button class="primary" id="publish">Publish</button></div></div>
</div>

<h2 class="sec">CODING</h2>
<div class="todo">
  <div class="card"><div class="big" id="t-unc">–</div><p id="t-uncp">bills without a code. Make a spreadsheet of them, fill in <code>subtopic</code>, and save it into the session's codes folder in <code>inputs/codes</code>.</p>
    <div class="row"><select id="ga" aria-label="Session"></select><a class="btn" id="opensheet" target="_blank" rel="noopener" hidden>Open coding sheet</a><button id="sheet">Make worksheet</button></div></div>
  <div class="card"><div class="big" id="t-rep">–</div><p>new think tank reports waiting for a code. Fill in <code>subtopic</code> in <code id="t-repf"></code> and delete rows that aren't reports.</p></div>
  <div class="card"><div class="big" id="t-law">–</div><div class="bar"><i id="t-lawbar" style="width:0"></i></div><p id="t-lawp">laws reviewed in Check law codes.</p>
    <a class="btn" href="/qc" target="_blank">Check law codes</a></div>
</div>
<p class="muted" style="font-size:12.5px;margin-top:8px">Counts are from the last check. New codes go on the site at the next <b>Update and check</b> and <b>Publish</b>.</p>

<h2 class="sec">OCCASIONALLY</h2>
<details class="more"><summary>Legislators and single steps</summary>
  <div class="occ">
    <div><div><h3>Legislators</h3><p>Party, district and official photo of every member, from the House and Senate member lists on ilga.gov. Run at the start of each session or when members change.</p></div>
      <div class="row"><button id="legs">Fetch legislators</button><a class="btn" href="/legislators" target="_blank">Review</a></div></div>
    <div><div><h3>Update bills from ILGA only</h3><p>The first part of Update and check: every bill's actions, sponsors and synopsis from ftp.ilga.gov.</p></div>
      <div class="row"><button id="acts">Update bills</button></div></div>
    <div><div><h3>Look for new think tank reports only</h3><p>The second part of Update and check. Sites that block automated requests are collected by hand.</p></div>
      <div class="row"><button id="tts">Look for reports</button></div></div>
  </div>
</details>
<pre id="log" hidden></pre>
<script>
const $ = s => document.querySelector(s);
const fmt = n => Number(n).toLocaleString('en-US');
const desc = {github:'Copies the site into the GitHub repository (docs/) and pushes it. GitHub Pages shows the new version within a minute or two.', zip:'Makes a dated zip of the site in build/releases to hand to IT or unzip into the web folder.', folder:'Copies the site into the web folder set in config.toml.', sftp:'Uploads the changed files to the web server set in config.toml.'};
async function poll(){
  const s = await fetch('/status').then(r=>r.json());
  const b = s.build, busy = s.job.running, notes = (s.job.notes||[]).map(n=>`<div class="note">${n}</div>`).join('');
  const failed = !busy && s.job.code;   // the last run stopped with an error: the results below it are from an earlier run
  $('#status').className = 'status ' + (busy ? '' : failed ? 'bad' : b ? (b.ok ? 'ok' : 'bad') : '');
  $('#status').innerHTML = busy ? `<b>Working: ${s.job.name}…</b>This page updates by itself. You can leave it open.`
    : failed ? `<b>${s.job.name[0].toUpperCase()+s.job.name.slice(1)} stopped with an error</b>The log below says what went wrong. Nothing was published.`
    : (b ? `<b>${b.ok ? 'Last check passed: ready to publish' : `Last check found ${b.errors} error${b.errors>1?'s':''}: open the report`}</b>Checked ${b.built}${b.warnings?` · ${b.warnings} warning${b.warnings>1?'s':''} in the report`:''}. ${s.published ? 'Last published '+s.published+'.' : 'Not published from this computer yet.'}` : '<b>Not checked yet</b>Start with step 1.') + notes;
  $('#version').textContent = 'Site code version ' + s.version + ' · updates from GitHub each time you open Update website';
  $('#ilga').textContent = s.ilga ? `Bills last updated from ILGA ${s.ilga}.` : 'Bills have not been updated from ILGA on this computer yet.';
  $('#pubdesc').textContent = desc[s.method] || '';
  $('#pw').hidden = s.method !== 'sftp';
  for (const id of ['update','build','sheet','legs','acts','tts']) $('#'+id).disabled = busy;
  $('#publish').disabled = busy || failed || !b || !b.ok;
  if (!$('#ga').options.length) $('#ga').innerHTML = s.sessions.slice().reverse().map(g=>`<option value="${g}">${g}th General Assembly</option>`).join('');
  const t = b?.todo, ga = $('#ga').value;
  if (s.sheet){ $('#opensheet').href = s.sheet; $('#opensheet').hidden = false; $('#sheet').className = 'link'; }
  if (t){ const unc = t.uncoded?.[ga] ?? 0; $('#t-unc').textContent = fmt(unc); $('#t-unc').className = 'big' + (unc ? '' : ' done');
    if (s.sheet) $('#t-uncp').textContent = `bills without a code${t.sheet?.[ga] != null ? `; ${fmt(t.sheet[ga])} bills (new or rewritten by an amendment) waiting in the coding sheet` : ''}. New and rewritten bills go into the sheet each night; codes there reach the site at the next update.`;
    $('#t-rep').textContent = fmt(t.reports_waiting||0); $('#t-rep').className = 'big' + (t.reports_waiting ? '' : ' done');
    const r = s.review?.[ga]; if (r){ $('#t-law').textContent = `${fmt(r.done)} / ${fmt(r.laws)}`; $('#t-law').className = 'big' + (r.done>=r.laws ? ' done' : '');
      $('#t-lawbar').style.width = (100*r.done/Math.max(1,r.laws)).toFixed(1)+'%';
      $('#t-lawp').textContent = `laws reviewed in Check law codes${r.pending ? `; ${r.pending} recode${r.pending>1?'s':''} waiting for the next check` : ''}.`; } }
  $('#t-repf').textContent = s.reports_file || 'the new-reports file';
  if (s.log.length){ const L = $('#log'); L.hidden = false; const atEnd = L.scrollTop + L.clientHeight >= L.scrollHeight - 8; L.textContent = s.log.join('\n'); if (atEnd) L.scrollTop = L.scrollHeight; }
  setTimeout(poll, busy ? 700 : 3000);
}
const post = (url, body) => fetch(url, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body||{})}).then(r=>r.json()).then(r=>{ if (!r.ok) alert(r.message); setTimeout(poll, 200); });
$('#update').onclick = () => post('/run/update');
$('#build').onclick = () => post('/run/build');
$('#sheet').onclick = () => post('/run/worksheet', {ga: +$('#ga').value});
$('#ga').onchange = () => poll();
$('#legs').onclick = () => post('/run/legislators');
$('#acts').onclick = () => post('/run/actions');
$('#tts').onclick = () => post('/run/reports');
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
        if path == '/qc':
            return self.send(200, qc.PAGE)
        if path == '/qc/data':
            live = [int(x['ga']) for x in CFG['bills']['sessions'] if x.get('status') != 'upcoming']
            ga = int((parse_qs(urlparse(self.path).query).get('ga') or [max(live)])[0])
            try:
                body = {**qc.payload(OUT, DATA, ga), 'sessions': sorted(live, reverse=True)}
            except Exception as e:  # noqa: BLE001 - shown on the page
                body = {'error': f'Could not read the laws from the last check ({e}). Run "Check data" again.', 'sessions': sorted(live, reverse=True)}
            return self.send(200, json.dumps(body), 'application/json')
        if path == '/report':
            f = OUT / 'report.html'
            return self.send(200, f.read_bytes()) if f.exists() else self.send(404, 'Run "Check data" first.')
        if path == '/legislators':
            f = OUT / 'legislators.html'
            return self.send(200, f.read_bytes()) if f.exists() else self.send(404, 'Run "Fetch legislators" first.')
        if path.startswith('/content/legislators/photos/'):  # images on the legislator review page
            base = (ROOT / 'content' / 'legislators' / 'photos').resolve()
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
        if path == '/qc/save':
            try:
                cb = json.loads((OUT / 'site' / 'data' / 'codebook.json').read_text(encoding='utf-8'))
                rows = qc.save(DATA, int(body['ga']), body.get('reviewer', ''), body.get('items', []), {int(k) for k in cb['subs']} | {-555})
                return self.send(200, json.dumps({'ok': True, 'rows': rows}), 'application/json')
            except Exception as e:  # noqa: BLE001 - shown to the reviewer
                return self.send(200, json.dumps({'ok': False, 'message': f'Not saved: {e}'}), 'application/json')
        if path == '/run/update':
            ok = run('updating and checking', None, steps=UPDATE)
        elif path == '/run/build':
            ok = run('checking data', ['pipeline/build.py'])
        elif path == '/run/worksheet':
            ok = run('making a worksheet', ['pipeline/build.py', '--worksheet', str(int(body.get('ga')))])
        elif path == '/run/legislators':
            ok = run('fetching legislators from ilga.gov', ['pipeline/fetch_legislators.py'])
        elif path == '/run/reports':
            ok = run('looking for new think tank reports', ['pipeline/fetch_reports.py'])
        elif path == '/run/actions':
            ok = run('updating bills from ftp.ilga.gov', ['pipeline/fetch_actions.py'])
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
