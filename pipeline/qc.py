"""Quality check of the subtopic codes on enacted laws: the "Check law codes" page of the Update website panel.

Reviewers go through every substantive bill that became law, one at a time or a subtopic at a time, and mark each code
correct, change it, or flag it as unsure. Each reviewer's decisions are saved in their own file in Dropbox,
    inputs/qc/<ga>/review_<reviewer>.csv   bill, decision (ok / recode / unsure), code, was, note, reviewer, reviewed_at
so several people can work at once. The build (build.py, apply_reviews) applies recodes on top of every other code and
reports reviewers who disagree; the site changes after the next Check data and Publish.
The page reads the laws from the last Check data (build/site/data).
"""
import csv, datetime as dt, json, re, threading
from pathlib import Path

COLS = ['bill', 'decision', 'code', 'was', 'note', 'reviewer', 'reviewed_at']
LOCK = threading.Lock()


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40]


def folder(data, ga):
    return data / 'inputs' / 'qc' / str(ga)


def read_reviews(data, ga):
    """Every reviewer's rows: [{bill, decision, code, was, note, reviewer, reviewed_at}]."""
    out = []
    for f in sorted(folder(data, ga).glob('review_*.csv')):
        with f.open(newline='', encoding='utf-8') as fh:
            out += list(csv.DictReader(fh))
    return out


def latest(rows):
    """{bill: the most recent decision on it by anyone}."""
    best = {}
    for r in rows:
        if r['bill'] not in best or r['reviewed_at'] > best[r['bill']]['reviewed_at']:
            best[r['bill']] = r
    return best


def laws(out, ga):
    """The session's enacted substantive bills from the last build, with what a reviewer needs to judge the code."""
    d = out / 'site' / 'data'
    s = json.loads((d / 'bills' / 'summary.json').read_text(encoding='utf-8'))
    syn = json.loads((d / 'bills' / str(ga) / 'details.json').read_text(encoding='utf-8'))
    sp = json.loads((d / 'bills' / str(ga) / 'sponsors.json').read_text(encoding='utf-8'))
    people = json.loads((d / 'people.json').read_text(encoding='utf-8'))['people']
    rows = []
    for r in s['rows']:
        x = dict(zip(s['cols'], r))
        if x['ga'] != ga or (x['stage'] or 0) < 5:
            continue
        chief = next((people[i] for i, ro in sp.get(x['key'], []) if ro == 'C'), None)
        rows.append({'bill': x['bill'], 'key': x['key'], 'short': x['short'], 'synopsis': syn.get(x['key'], ''), 'sub': x['sub'],
                     'pa': x['pa'], 'law_date': x['law_date'], 'chamber': x['chamber'],
                     'sponsor': f'{chief[0]}. {chief[1]}' if chief else '',
                     'url': f'https://ilga.gov/ftp/legislation/{ga}/BillStatus/HTML/{x["link"]}.html' if re.match(r'^\d+00[A-Z]+\d+$', x['link'] or '') else x['link']})
    return sorted(rows, key=lambda r: (r['bill'].split()[0], int(r['bill'].split()[1])))


def payload(out, data, ga):
    d = out / 'site' / 'data'
    if not (d / 'bills' / 'summary.json').exists():
        return {'error': 'Run "Check data" first: this page reads the laws from the last check.'}
    cb = json.loads((d / 'codebook.json').read_text(encoding='utf-8'))
    rules = {}
    doc = d / 'codebook_doc.json'
    if doc.exists():
        for m in json.loads(doc.read_text(encoding='utf-8')).get('majors', []):
            for sb in m.get('subs', []):
                rules[str(sb['code'])] = {'rule': sb.get('rule', ''), 'see': sb.get('see', ''), 'notes': sb.get('notes', [])}
    built = json.loads((out / 'status.json').read_text()).get('built') if (out / 'status.json').exists() else ''
    return {'ga': ga, 'built': built, 'laws': laws(out, ga), 'majors': cb['majors'], 'subs': cb['subs'], 'rules': rules,
            'reviews': latest(read_reviews(data, ga))}


def save(data, ga, reviewer, items, valid):
    """items: [{bill, decision, code, was, note}]. Rewrites this reviewer's file with their latest row per bill."""
    if not slug(reviewer):
        raise ValueError('Enter your name first.')
    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for it in items:
        if it['decision'] not in ('ok', 'recode', 'unsure'):
            raise ValueError('Unknown decision')
        if it['decision'] == 'recode' and int(it['code']) not in valid:
            raise ValueError(f'{it["code"]} is not in the codebook')
    f = folder(data, ga) / f'review_{slug(reviewer)}.csv'
    with LOCK:
        f.parent.mkdir(parents=True, exist_ok=True)
        mine = {}
        if f.exists():
            with f.open(newline='', encoding='utf-8') as fh:
                mine = {r['bill']: r for r in csv.DictReader(fh)}
        for it in items:
            mine[it['bill']] = {'bill': it['bill'], 'decision': it['decision'], 'code': str(it.get('code') or ''), 'was': str(it.get('was') if it.get('was') is not None else ''),
                                'note': (it.get('note') or '').strip(), 'reviewer': reviewer.strip(), 'reviewed_at': now}
        tmp = f.with_suffix('.tmp')
        with tmp.open('w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, COLS)
            w.writeheader()
            w.writerows(sorted(mine.values(), key=lambda r: (r['bill'].split()[0], int(r['bill'].split()[1]))))
        tmp.replace(f)
    return [mine[it['bill']] for it in items]


PAGE = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Check law codes · IPAP</title>
<style>
:root{--ink:#1b2330;--ink2:#4b5566;--muted:#7b8494;--line:#e4e9f0;--sunk:#f4f7fb;--accent:#2c67ad;--btn:#dcebfb;--btnink:#1d4f8a;--ok:#1f7a4d;--okbg:#e2f5ec;--bad:#b3261e;--badbg:#fde8e6;--warn:#8a5a00;--warnbg:#fff4d6;--navy:#13294b}
*{box-sizing:border-box} body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:#fff;margin:0}
header{position:sticky;top:0;z-index:5;background:var(--navy);color:#fff;padding:10px 20px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0;font-weight:700} header a{color:#cfe0f5} header input,header select{font:inherit;padding:5px 8px;border-radius:6px;border:0}
.prog{flex:1 1 260px;display:flex;align-items:center;gap:10px;font-size:13.5px} .bar{flex:1;height:8px;background:rgba(255,255,255,.2);border-radius:4px;overflow:hidden;display:flex}
.bar i{display:block;height:100%} main{max-width:1180px;margin:0 auto;padding:16px 20px 60px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:12px} .tabs button,.btn{font:inherit;border:1px solid #cfd7e2;background:#fff;border-radius:7px;padding:7px 12px;cursor:pointer;color:var(--ink)}
.tabs button[aria-pressed=true]{background:var(--navy);color:#fff;border-color:var(--navy)} select,input[type=search],input[type=text]{font:inherit;padding:7px 9px;border:1px solid #cfd7e2;border-radius:7px}
.btn:disabled{opacity:.45;cursor:default} .btn.ok{background:var(--okbg);border-color:#9fd8bb;color:var(--ok);font-weight:700} .btn.re{background:var(--btn);border-color:#a9c9ee;color:var(--btnink);font-weight:700}
.btn.un{background:var(--warnbg);border-color:#efd28a;color:var(--warn);font-weight:700} kbd{font:11px ui-monospace,Menlo,monospace;border:1px solid #cfd7e2;border-bottom-width:2px;border-radius:4px;padding:0 4px;margin-left:6px;color:var(--muted);background:#fff}
.card{border:1px solid var(--line);border-radius:12px;padding:18px 20px;display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:22px}
.bill{font-size:13px;color:var(--muted)} .bill b{color:var(--ink);font:700 15px ui-monospace,Menlo,monospace} h2{font-size:21px;margin:4px 0 10px;line-height:1.3}
.syn{max-height:56vh;overflow:auto;font-size:14.5px;line-height:1.6} .syn .v{background:var(--sunk);border-radius:8px;padding:10px 14px;margin-bottom:8px;color:var(--ink2)}
.syn .v.last{background:#fff;border:2px solid var(--navy);color:var(--ink)} .syn .vh{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:2px} .syn .last .vh{color:var(--navy)}
.syn details summary{cursor:pointer;font-size:13px;color:var(--accent);margin-bottom:8px}
.code{font-size:13px;color:var(--muted)} .code .num{font:700 26px ui-monospace,Menlo,monospace;color:var(--navy)} .code .nm{font-size:17px;font-weight:700;color:var(--ink);display:block;margin-top:2px}
.rule{font-size:13.5px;color:var(--ink2);background:var(--sunk);border-radius:8px;padding:10px 12px;margin-top:10px;max-height:28vh;overflow:auto} .rule b{color:var(--ink)}
.acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px} .tag{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:2px 9px}
.t-ok{background:var(--okbg);color:var(--ok)} .t-recode{background:var(--btn);color:var(--btnink)} .t-unsure{background:var(--warnbg);color:var(--warn)} .t-none{background:var(--sunk);color:var(--muted)}
.pick{position:relative;margin-top:12px} .pick input{width:100%} .res{border:1px solid #cfd7e2;border-radius:8px;margin-top:4px;max-height:300px;overflow:auto;background:#fff}
.res div{padding:6px 10px;cursor:pointer;display:flex;gap:10px} .res div.on{background:var(--btn)} .res .c{font:700 13px ui-monospace,Menlo,monospace;min-width:44px} .res .mj{color:var(--muted);font-size:12px;margin-left:auto;white-space:nowrap}
.layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:18px} .subs{border:1px solid var(--line);border-radius:10px;max-height:78vh;overflow:auto}
.subs .mh{position:sticky;top:0;background:#fff;font-size:12px;font-weight:700;color:var(--muted);padding:8px 10px 4px;border-bottom:1px solid var(--line)}
.subs button{display:flex;width:100%;text-align:left;border:0;background:none;padding:6px 10px;font:inherit;font-size:13.5px;cursor:pointer;gap:8px}
.subs button.on{background:var(--btn)} .subs .n{margin-left:auto;color:var(--muted);font-size:12px;white-space:nowrap} .subs .done{color:var(--ok)}
table{width:100%;border-collapse:collapse} td,th{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top;text-align:left;font-size:14px} th{font-size:12px;color:var(--muted)}
td.t{cursor:pointer} td.t .s{display:none;color:var(--ink2);font-size:13px;margin-top:4px} td.t .syn{font-size:13px;max-height:none} tr.open td.t .s{display:block}
.rowacts{display:flex;gap:4px;white-space:nowrap} .rowacts .btn{padding:3px 8px;font-size:13px} .note{font-size:12.5px;color:var(--warn)}
.empty{padding:40px;text-align:center;color:var(--muted)} .msg{margin:0 0 12px;padding:10px 14px;border-radius:8px;background:var(--warnbg);color:var(--warn);font-size:14px}
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;padding:8px 14px;border-radius:8px;font-size:14px;opacity:0;transition:opacity .2s;pointer-events:none} .toast.on{opacity:1}
@media (max-width:860px){.card,.layout{grid-template-columns:minmax(0,1fr)} .subs{max-height:240px}}
</style>
<header><h1>Check law codes</h1><select id="ga" aria-label="Session"></select>
  <label>Your name <input id="who" placeholder="Name" size="14"></label>
  <div class="prog"><div class="bar" id="bar"></div><span id="ptxt"></span></div><a href="/">Back to the panel</a></header>
<main>
  <div id="msg"></div>
  <div class="tabs"><button data-mode="one" aria-pressed="true">One at a time</button><button data-mode="sub" aria-pressed="false">By subtopic</button>
    <span style="width:12px"></span>
    <select id="show" aria-label="Show"><option value="todo">Not yet reviewed</option><option value="all">All laws</option><option value="recode">Recoded</option><option value="unsure">Unsure</option><option value="ok">Marked correct</option></select>
    <select id="maj" aria-label="Topic"><option value="">All topics</option></select>
    <input type="search" id="q" placeholder="Find a bill or word" style="flex:1 1 180px"></div>
  <div id="view"></div>
</main>
<div class="toast" id="toast"></div>
<script>
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let D = null, S = {mode:'one', show:'todo', maj:'', q:'', i:0, sub:null, pick:null};
const who = () => $('#who').value.trim();
try { $('#who').value = localStorage.getItem('qc-who') || ''; } catch {}
$('#who').addEventListener('change', ()=>{ try { localStorage.setItem('qc-who', who()); } catch {} });
const toast = t => { const e = $('#toast'); e.textContent = t; e.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(()=>e.classList.remove('on'), 1600); };
const subName = c => c == null ? 'Not coded' : +c === -555 ? 'No policy content' : (D.subs[c] || 'Subtopic ' + c);
const majOf = c => c == null || +c < 0 ? null : Math.floor(+c / 100);
const rev = b => D.reviews[b.bill];
/* the synopsis holds each version of the bill, joined by "; ": as introduced, then each amendment in order. An amendment
   that "replaces everything after the enacting clause" rewrote the bill, so the law is mostly what the last of those says. */
function synHTML(t){
  const v = String(t||'').split(/(?<=\.);\s+(?=[A-Z])/).map(x=>x.trim()).filter(Boolean);
  if (!v.length) return '<div class="v">No synopsis.</div>';
  const rw = v.map(x=>/^Replaces everything after the enacting clause/i.test(x)), main = rw.lastIndexOf(true);
  return v.map((x,i)=>`<div class="v ${i===(main<0?0:main)?'last':''}"><div class="vh">${i?`Amendment ${i}${rw[i]?' · rewrote the bill':''}`:'As introduced'}${i===main?' · main text of the law':''}</div>${esc(x)}</div>`).join('');
}
const current = b => { const r = rev(b); return r && r.decision === 'recode' ? +r.code : b.sub; };   // a recode shows at once, before the next build
function list(){
  const q = S.q.toLowerCase();
  return D.laws.filter(b=>{ const r = rev(b), st = r ? r.decision : 'todo';
    if (S.show === 'todo' ? r : S.show !== 'all' && st !== S.show) return false;
    if (S.maj && majOf(current(b)) !== +S.maj) return false;
    return !q || (b.bill + ' ' + b.short + ' ' + b.synopsis + ' ' + (b.pa||'')).toLowerCase().includes(q); });
}
async function load(){
  const ga = $('#ga').value || '';
  const r = await fetch('/qc/data' + (ga ? '?ga=' + ga : '')).then(r=>r.json());
  if (r.sessions && !$('#ga').options.length) $('#ga').innerHTML = r.sessions.map(g=>`<option value="${g}">${g}th General Assembly</option>`).join('');
  if (r.error){ $('#view').innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
  D = r; $('#ga').value = r.ga;
  $('#maj').innerHTML = '<option value="">All topics</option>' + Object.entries(D.majors).map(([k,v])=>`<option value="${k}">${k} · ${esc(v)}</option>`).join('');
  $('#maj').value = S.maj; draw();
}
function progress(){
  const n = D.laws.length, c = {ok:0, recode:0, unsure:0}; for (const b of D.laws){ const r = rev(b); if (r) c[r.decision]++; }
  const done = c.ok + c.recode + c.unsure, w = k => `<i style="width:${100*c[k]/Math.max(n,1)}%;background:${{ok:'#5cc493',recode:'#7fb2ec',unsure:'#f0c75e'}[k]}"></i>`;
  $('#bar').innerHTML = w('ok') + w('recode') + w('unsure');
  $('#ptxt').textContent = `${done} of ${n} laws reviewed · ${c.recode} recoded · ${c.unsure} unsure`;
  const pend = Object.values(D.reviews).filter(r=>r.reviewed_at > (D.built||'')).filter(r=>r.decision==='recode').length;
  $('#msg').innerHTML = pend ? `<p class="msg">${pend} recode${pend>1?'s':''} saved since the last Check data (${esc(D.built)}). Run <b>Check data</b> and <b>Publish</b> in the panel to put ${pend>1?'them':'it'} on the website.</p>` : '';
}
function tag(r){ if (!r) return '<span class="tag t-none">Not reviewed</span>';
  const t = {ok:'Correct', recode:'Recoded', unsure:'Unsure'}[r.decision];
  return `<span class="tag t-${r.decision}">${t}</span> <span class="code">${esc(r.reviewer)}, ${esc(r.reviewed_at.slice(0,16))}${r.decision==='recode'?` · was ${esc(r.was)}`:''}</span>${r.note?`<div class="note">${esc(r.note)}</div>`:''}`; }
function ruleHTML(c){ const r = D.rules[String(c)]; if (!r) return '';
  return `<div class="rule"><b>Codebook rule.</b> ${esc(r.rule||'')}${r.see?`<div style="margin-top:6px"><b>See also:</b> ${esc(r.see)}</div>`:''}${(r.notes||[]).map(n=>`<div style="margin-top:6px"><b>Note:</b> ${esc(n)}</div>`).join('')}</div>`; }
function codeHTML(c){ const m = majOf(c);
  return `<div class="code"><span class="num">${c ?? '—'}</span><span class="nm">${esc(subName(c))}</span>${m!=null?`${m} · ${esc(D.majors[m]||'')}`:''}</div>`; }
async function decide(items){
  if (!who()){ $('#who').focus(); toast('Enter your name first'); return false; }
  const r = await fetch('/qc/save', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({ga:D.ga, reviewer:who(), items})}).then(r=>r.json());
  if (!r.ok){ alert(r.message); return false; }
  for (const row of r.rows) D.reviews[row.bill] = row;
  return true;
}
/* ---------- code picker: type a number or words; arrows and Enter to choose ---------- */
function picker(el, b, done){
  const all = [[-555, 'No policy content'], ...Object.entries(D.subs).map(([k,v])=>[+k, v])];
  el.innerHTML = `<div class="pick"><input type="text" id="pk" placeholder="Type a code or words, e.g. 1520 or prescription drugs" autocomplete="off"><div class="res" id="pkr"></div><div id="pkrule"></div></div>`;
  const inp = $('#pk'), res = $('#pkr'); let hits = [], on = 0;
  const show = () => { const q = inp.value.trim().toLowerCase(), words = q.split(/\s+/).filter(Boolean);
    hits = all.filter(([c,n])=>!q || String(c).startsWith(q) || words.every(w=>(n+' '+(D.majors[majOf(c)]||'')).toLowerCase().includes(w))).slice(0, 60); on = 0; paint(); };
  const paint = () => { res.innerHTML = hits.map(([c,n],i)=>`<div class="${i===on?'on':''}" data-i="${i}"><span class="c">${c}</span><span>${esc(n)}</span><span class="mj">${esc(D.majors[majOf(c)]||'')}</span></div>`).join('') || '<div>No match</div>';
    res.querySelector('.on')?.scrollIntoView({block:'nearest'}); $('#pkrule').innerHTML = hits[on] ? ruleHTML(hits[on][0]) : ''; };
  inp.addEventListener('input', show);
  inp.addEventListener('keydown', e=>{ if (e.key==='ArrowDown'){ on=Math.min(hits.length-1,on+1); paint(); e.preventDefault(); } else if (e.key==='ArrowUp'){ on=Math.max(0,on-1); paint(); e.preventDefault(); }
    else if (e.key==='Enter' && hits[on]){ e.preventDefault(); done(hits[on][0]); } else if (e.key==='Escape'){ el.innerHTML=''; e.stopPropagation(); } });
  res.addEventListener('click', e=>{ const d = e.target.closest('[data-i]'); if (d) done(hits[+d.dataset.i][0]); });
  show(); inp.focus();
}
/* ---------- one at a time ---------- */
function drawOne(){
  const L = list(); S.i = Math.max(0, Math.min(S.i, L.length-1));
  if (!L.length){ $('#view').innerHTML = `<div class="empty">${S.show==='todo'?'Every law here has been reviewed. 🎉':'No laws match.'}</div>`; return; }
  const b = L[S.i], c = current(b);
  $('#view').innerHTML = `<div class="card"><div>
      <div class="bill"><b>${esc(b.bill)}</b> · Public Act ${esc(b.pa||'—')} · ${esc(b.law_date||'')} · ${esc(b.sponsor)} · <a href="${esc(b.url)}" target="_blank" rel="noopener">ILGA bill status</a></div>
      <h2>${esc(b.short)}</h2><div class="syn">${synHTML(b.synopsis)}</div></div>
    <div><div class="code" style="margin-bottom:4px">Current code</div>${codeHTML(c)}${ruleHTML(c)}<div style="margin-top:10px">${tag(rev(b))}</div>
      <div class="acts"><button class="btn ok" id="a-ok">✓ Correct<kbd>Enter</kbd></button><button class="btn re" id="a-re">Change code<kbd>C</kbd></button><button class="btn un" id="a-un">Unsure<kbd>U</kbd></button></div>
      <div class="acts"><button class="btn" id="a-prev" ${S.i?'':'disabled'}>← Back</button><button class="btn" id="a-next" ${S.i<L.length-1?'':'disabled'}>Skip →</button><span class="code" style="align-self:center">${S.i+1} of ${L.length} shown</span></div>
      <div id="pkbox"></div></div></div>`;
  const step = async (items) => { if (await decide(items)){ const moved = S.show !== 'all' && S.show !== items[0].decision; if (!moved) S.i++; progress(); drawOne(); } };
  $('#a-ok').onclick = () => step([{bill:b.bill, decision:'ok', code:c, was:c}]);
  $('#a-re').onclick = () => picker($('#pkbox'), b, code => { if (code === c) return step([{bill:b.bill, decision:'ok', code:c, was:c}]); step([{bill:b.bill, decision:'recode', code, was:c}]); });
  $('#a-un').onclick = () => { const note = prompt('What makes this one unsure? (optional)', rev(b)?.note || ''); if (note !== null) step([{bill:b.bill, decision:'unsure', code:c, was:c, note}]); };
  $('#a-prev').onclick = () => { S.i--; drawOne(); }; $('#a-next').onclick = () => { S.i++; drawOne(); };
}
document.addEventListener('keydown', e=>{
  if (S.mode !== 'one' || !D || e.target.closest('input,select,textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase(), click = id => { const b = $(id); if (b && !b.disabled){ e.preventDefault(); b.click(); } };
  if (k === 'enter') click('#a-ok'); else if (k === 'c') click('#a-re'); else if (k === 'u') click('#a-un'); else if (k === 'arrowright') click('#a-next'); else if (k === 'arrowleft') click('#a-prev');
});
/* ---------- by subtopic ---------- */
function drawSub(){
  const L = list(), by = new Map();
  for (const b of L){ const c = current(b); if (!by.has(c)) by.set(c, []); by.get(c).push(b); }
  const codes = [...by.keys()].sort((a,b)=>(a??1e9)-(b??1e9));
  if (!codes.length){ $('#view').innerHTML = `<div class="empty">${S.show==='todo'?'Every law here has been reviewed. 🎉':'No laws match.'}</div>`; return; }
  if (!by.has(S.sub)) S.sub = codes[0];
  let side = '', lastM;
  for (const c of codes){ const m = majOf(c); if (m !== lastM){ side += `<div class="mh">${m==null?'Other':m+' · '+esc(D.majors[m]||'')}</div>`; lastM = m; }
    const all = D.laws.filter(b=>current(b)===c), done = all.filter(b=>rev(b)).length;
    side += `<button data-c="${c}" class="${c===S.sub?'on':''}"><span>${c ?? '—'} ${esc(subName(c))}</span><span class="n ${done===all.length?'done':''}">${by.get(c).length}${S.show!=='all'?'':''} · ${done}/${all.length}</span></button>`; }
  const rows = by.get(S.sub), todo = rows.filter(b=>!rev(b));
  $('#view').innerHTML = `<div class="layout"><div class="subs">${side}</div><div>
    <div style="display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-bottom:6px"><div style="flex:1 1 260px">${codeHTML(S.sub)}${ruleHTML(S.sub)}</div>
      <button class="btn ok" id="s-all" ${todo.length?'':'disabled'}>✓ Mark the ${todo.length} unreviewed correct</button></div>
    <p class="code" style="margin:4px 0 8px">Click a title for its synopsis. Change any that don't fit, then mark the rest correct.</p>
    <table><thead><tr><th>Bill</th><th>Title</th><th>Status</th><th></th></tr></thead><tbody>${rows.map(b=>`<tr data-b="${esc(b.bill)}"><td style="white-space:nowrap"><b>${esc(b.bill)}</b><div class="code">PA ${esc(b.pa||'—')}</div></td>
      <td class="t"><b>${esc(b.short)}</b><div class="s"><div class="syn">${synHTML(b.synopsis)}</div><div style="margin-top:4px"><a href="${esc(b.url)}" target="_blank" rel="noopener">ILGA bill status</a></div></div></td>
      <td style="min-width:150px">${tag(rev(b))}</td><td><div class="rowacts"><button class="btn ok" data-a="ok" title="Correct">✓</button><button class="btn re" data-a="re" title="Change code">Change</button><button class="btn un" data-a="un" title="Unsure">?</button></div><div class="pkrow"></div></td></tr>`).join('')}</tbody></table></div></div>`;
  $$('.subs button').forEach(x=>x.onclick = () => { S.sub = x.dataset.c === 'null' ? null : +x.dataset.c; drawSub(); });
  $$('td.t').forEach(td=>td.onclick = e => { if (!e.target.closest('a')) td.parentElement.classList.toggle('open'); });
  const redraw = () => { progress(); drawSub(); };
  $$('tr[data-b]').forEach(tr=>{ const b = D.laws.find(x=>x.bill===tr.dataset.b), c = current(b);
    tr.querySelector('[data-a=ok]').onclick = async () => { if (await decide([{bill:b.bill, decision:'ok', code:c, was:c}])) redraw(); };
    tr.querySelector('[data-a=un]').onclick = async () => { const note = prompt('What makes this one unsure? (optional)', rev(b)?.note || ''); if (note !== null && await decide([{bill:b.bill, decision:'unsure', code:c, was:c, note}])) redraw(); };
    tr.querySelector('[data-a=re]').onclick = () => picker(tr.querySelector('.pkrow'), b, async code => { if (await decide([code===c ? {bill:b.bill, decision:'ok', code:c, was:c} : {bill:b.bill, decision:'recode', code, was:c}])){ toast(code===c ? 'Marked correct' : `${b.bill} recoded to ${code}`); redraw(); } }); });
  $('#s-all').onclick = async () => { if (await decide(todo.map(b=>({bill:b.bill, decision:'ok', code:current(b), was:current(b)})))){ toast(`${todo.length} marked correct`); redraw(); } };
}
function draw(){ progress(); (S.mode === 'one' ? drawOne : drawSub)(); }
$$('[data-mode]').forEach(b=>b.onclick = () => { S.mode = b.dataset.mode; $$('[data-mode]').forEach(x=>x.setAttribute('aria-pressed', x===b)); draw(); });
$('#show').onchange = e => { S.show = e.target.value; S.i = 0; draw(); };
$('#maj').onchange = e => { S.maj = e.target.value; S.i = 0; S.sub = undefined; draw(); };
let deb; $('#q').oninput = e => { clearTimeout(deb); deb = setTimeout(()=>{ S.q = e.target.value.trim(); S.i = 0; draw(); }, 150); };
$('#ga').onchange = () => { S.i = 0; load(); };
load();
</script>'''
