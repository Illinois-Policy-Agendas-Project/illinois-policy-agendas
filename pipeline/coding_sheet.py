#!/usr/bin/env python3
"""The Google coding sheet: where coders code new bills and bills an amendment rewrote.

    python pipeline/coding_sheet.py pull    before the check: copy the sheet's codes to inputs/coding_sheet/<ga>.csv,
                                            which the check reads (codes there override the bill file and coding files)
    python pipeline/coding_sheet.py push    after the check: add to the sheet every bill introduced or rewritten since
                                            the last run, from build/coding_queue.json

The sheet has two tabs per session and a Codebook tab that feeds the lists of codes. In "104th GA", each bill goes
in once when it is introduced, and again whenever an amendment replaces everything after the enacting clause (unless it
only reinserts the introduced bill): its row is updated, the earlier code moves to "Code before" and Subtopic is cleared.
Rows coders delete don't come back. What has been added is remembered in state/coding_sheet.json.

"104th GA laws" is the sheet's version of Check law codes: every law, grouped by code, with the codebook rule. A
reviewer picks a Decision (Correct, Change code with a New code, or Unsure). pull saves the decisions as a review file,
inputs/qc/<ga>/review_coding-sheet.csv, which the check applies like any reviewer's (see qc.py).

Settings (GitHub secret / config.toml):
    GOOGLE_SERVICE_ACCOUNT   the service account's JSON key (see the README); without it both commands do nothing
    [coding_sheet] id        the sheet's ID, the long part of its link between /d/ and /edit (or GOOGLE_SHEET_ID)
Only the Python standard library and `cryptography` (installed with paramiko) are used.
"""
import base64, csv, datetime as dt, json, os, re, sys, time, tomllib, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
CFG = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
STATE = DATA / 'state' / 'coding_sheet.json'
API = 'https://sheets.googleapis.com/v4/spreadsheets'
COLS = ['Bill', 'Why', 'Added', 'Short description', 'What the bill does now', 'Code before', 'Subtopic', 'Coder', 'Notes', 'ILGA']
WIDTHS = [80, 170, 90, 200, 520, 200, 220, 90, 200, 70]
LAW_COLS = ['Bill', 'Public Act', 'Short description', 'What the law does', 'Code', 'Rule for this code', 'Decision', 'New code', 'Reviewer', 'Notes', 'ILGA']
LAW_WIDTHS = [80, 90, 200, 520, 220, 320, 120, 220, 90, 200, 70]
DECISIONS = {'Correct': 'ok', 'Change code': 'recode', 'Unsure': 'unsure'}
REVIEW_COLS = ['bill', 'decision', 'code', 'was', 'note', 'reviewer', 'reviewed_at']   # as in qc.py
CODEBOOK = 'Codebook'
ROWS = 5000   # rows of each session tab that get the Subtopic list


def tab(ga):
    return f'{ga}th GA'


def law_tab(ga):
    return f'{ga}th GA laws'


def col(i):
    return chr(ord('A') + i)


def label(code, names):
    """How a code appears in the sheet's list: "1201 Criminal Code"."""
    return '' if code is None else f'{code} {names.get(code, "")}'.strip()


def parse_code(v):
    """The code at the start of a Subtopic cell ("1201 Criminal Code", "1201", 1201.0), or the text as is."""
    v = str(v or '').strip().lstrip("'")
    m = re.match(r'^(-?\d+)(?:\.0)?\b', v)
    return m.group(1) if m else v


def bill_order(b):
    t, _, n = b.partition(' ')
    return (t, int(n) if n.isdigit() else 0)


class Sheets:
    """The few Google Sheets API calls the sheet needs, signed in as the service account."""

    def __init__(self, sheet_id, key):
        self.id, self.key, self.tok, self.exp = sheet_id, key, None, 0

    def token(self):
        if self.tok and time.time() < self.exp - 60:
            return self.tok
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=')
        now = int(time.time())
        uri = self.key.get('token_uri', 'https://oauth2.googleapis.com/token')
        msg = b64(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode()) + b'.' + b64(json.dumps(
            {'iss': self.key['client_email'], 'scope': 'https://www.googleapis.com/auth/spreadsheets', 'aud': uri, 'iat': now, 'exp': now + 3600}).encode())
        pk = serialization.load_pem_private_key(self.key['private_key'].encode(), None)
        jwt = msg + b'.' + b64(pk.sign(msg, padding.PKCS1v15(), hashes.SHA256()))
        body = urllib.parse.urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': jwt.decode()}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(uri, data=body), timeout=60) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise SystemExit(f'Google refused the sign-in ({e.code}): {e.read().decode("utf-8", "replace")[:300]}\n'
                             'Check that GOOGLE_SERVICE_ACCOUNT holds the whole JSON key file.') from None
        self.tok, self.exp = res['access_token'], time.time() + res.get('expires_in', 3600)
        return self.tok

    def call(self, method, path, body=None, query=None):
        url = f'{API}/{self.id}{path}' + ('?' + urllib.parse.urlencode(query) if query else '')
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, method=method, headers={'Authorization': f'Bearer {self.token()}', 'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read() or b'{}')
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                detail = e.read().decode('utf-8', 'replace')[:400]
                if 'SERVICE_DISABLED' in detail or 'has not been used in project' in detail:
                    raise SystemExit('The Google Sheets API is not turned on for the service account\'s Cloud project. In the Cloud console: '
                                     'APIs & Services → Library → Google Sheets API → Enable, wait a few minutes, and try again.') from None
                if e.code in (403, 404):
                    raise SystemExit(f'Google Sheets said {e.code}: {detail}\nShare the sheet with {self.key["client_email"]} as an Editor, '
                                     'and check [coding_sheet] id in config.toml.') from None
                raise SystemExit(f'Google Sheets said {e.code}: {detail}') from None

    def tabs(self):
        """{tab title: sheetId}"""
        meta = self.call('GET', '', query={'fields': 'sheets.properties(sheetId,title)'})
        return {s['properties']['title']: s['properties']['sheetId'] for s in meta.get('sheets', [])}

    def batch(self, requests):
        return self.call('POST', ':batchUpdate', {'requests': requests}) if requests else {}

    def values(self, rng):
        return self.call('GET', f'/values/{urllib.parse.quote(rng)}', query={'valueRenderOption': 'FORMATTED_VALUE'}).get('values', [])

    def append(self, rng, rows):
        return self.call('POST', f'/values/{urllib.parse.quote(rng)}:append', {'values': rows},
                         {'valueInputOption': 'USER_ENTERED', 'insertDataOption': 'INSERT_ROWS'})

    def write(self, data):
        """data: [(A1 range, [[values]])]"""
        return self.call('POST', '/values:batchUpdate', {'valueInputOption': 'USER_ENTERED', 'data': [{'range': r, 'values': v} for r, v in data]})

    def clear(self, rng):
        return self.call('POST', f'/values/{urllib.parse.quote(rng)}:clear', {})


def connect():
    key = os.environ.get('GOOGLE_SERVICE_ACCOUNT', '').strip()
    sheet_id = os.environ.get('GOOGLE_SHEET_ID') or CFG.get('coding_sheet', {}).get('id', '')
    if not key or not sheet_id:
        print('The coding sheet is not set up here (needs GOOGLE_SERVICE_ACCOUNT and [coding_sheet] id); skipped. '
              'The check uses the copy of its codes from the last run.')
        return None
    try:
        info = json.loads(key)
    except ValueError:
        raise SystemExit('GOOGLE_SERVICE_ACCOUNT is not a JSON key. Paste the whole contents of the .json file you downloaded.') from None
    return Sheets(sheet_id.strip(), info)


def live_sessions():
    return [int(s['ga']) for s in CFG['bills']['sessions'] if s.get('status') != 'upcoming']


def read_tab(sh, title):
    """(header positions {column name: index}, [row lists]) of a tab; the header stays as coders arranged it."""
    rows = sh.values(f"'{title}'!A1:Z")
    if not rows:
        return {}, []
    pos = {h.strip(): i for i, h in enumerate(rows[0]) if str(h).strip()}
    return pos, rows[1:]


def cell(row, pos, name):
    i = pos.get(name)
    return row[i] if i is not None and i < len(row) else ''


def write_csv(f, cols, rows):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix('.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(f)


def pull(sh):
    tabs = sh.tabs()
    for ga in live_sessions():
        if tab(ga) in tabs:
            pos, rows = read_tab(sh, tab(ga))
            if 'Bill' not in pos or 'Subtopic' not in pos:
                raise SystemExit(f'The "{tab(ga)}" tab needs the columns Bill and Subtopic in its first row.')
            out = [{'bill': cell(r, pos, 'Bill').strip(), 'subtopic': parse_code(cell(r, pos, 'Subtopic')), 'coder': cell(r, pos, 'Coder'),
                    'notes': cell(r, pos, 'Notes'), 'why': cell(r, pos, 'Why'), 'added': cell(r, pos, 'Added')} for r in rows]
            out = [r for r in out if r['bill']]
            f = DATA / 'inputs' / 'coding_sheet' / f'{ga}.csv'
            write_csv(f, ['bill', 'subtopic', 'coder', 'notes', 'why', 'added'], out)
            print(f'{tab(ga)}: {len(out):,} bills in the sheet, {sum(1 for r in out if r["subtopic"]):,} coded. Saved to {f}.')
        if law_tab(ga) in tabs:
            pull_laws(sh, ga)


def pull_laws(sh, ga):
    """The laws tab's decisions, as a "Check law codes" review file (inputs/qc/<ga>/review_coding-sheet.csv), so the
    check applies them like any reviewer's. A decision keeps the time it was first seen, so the most recent decision
    still wins between the sheet and the Check law codes page."""
    pos, rows = read_tab(sh, law_tab(ga))
    for need in ('Bill', 'Code', 'Decision', 'New code'):
        if need not in pos:
            raise SystemExit(f'The "{law_tab(ga)}" tab needs a "{need}" column in its first row.')
    f = DATA / 'inputs' / 'qc' / str(ga) / 'review_coding-sheet.csv'
    old = {}
    if f.exists():
        with f.open(newline='', encoding='utf-8') as fh:
            old = {r['bill']: r for r in csv.DictReader(fh)}
    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out, skipped = [], []
    for r in rows:
        bill = cell(r, pos, 'Bill').strip()
        new = parse_code(cell(r, pos, 'New code'))
        decision = DECISIONS.get(cell(r, pos, 'Decision').strip()) or ('recode' if new else None)
        if not bill or not decision:
            continue
        if decision == 'recode' and not new:
            skipped.append(bill)
            continue
        was = parse_code(cell(r, pos, 'Code'))
        row = {'bill': bill, 'decision': decision, 'code': new if decision == 'recode' else was, 'was': was,
               'note': cell(r, pos, 'Notes').strip(), 'reviewer': cell(r, pos, 'Reviewer').strip() or 'Coding sheet', 'reviewed_at': now}
        prev = old.get(bill)
        if prev and all(prev[k] == row[k] for k in ('decision', 'code', 'note', 'reviewer')):
            row['reviewed_at'] = prev['reviewed_at']
        out.append(row)
    write_csv(f, REVIEW_COLS, sorted(out, key=lambda r: bill_order(r['bill'])))
    print(f'{law_tab(ga)}: {len(out):,} of {len(rows):,} laws reviewed in the sheet, '
          f'{sum(1 for r in out if r["decision"] == "recode"):,} with a changed code. Saved to {f}.')
    if skipped:
        print(f'  "Change code" without a new code (ignored until one is picked): {", ".join(skipped)}')


def header_format(sid, cols, widths, wrap):
    """Requests that lay out a new tab: bold header, a filter, column widths, and a long text column that stays one line."""
    reqs = [{'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}, 'backgroundColor': {'red': .93, 'green': .95, 'blue': .98}}},
                            'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
            {'setBasicFilter': {'filter': {'range': {'sheetId': sid}}}}]
    reqs += [{'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 1, 'startColumnIndex': cols.index(c), 'endColumnIndex': cols.index(c) + 1},
                             'cell': {'userEnteredFormat': {'wrapStrategy': 'CLIP', 'verticalAlignment': 'TOP'}}, 'fields': 'userEnteredFormat(wrapStrategy,verticalAlignment)'}}
             for c in wrap]
    reqs += [{'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': i, 'endIndex': i + 1},
                                            'properties': {'pixelSize': w}, 'fields': 'pixelSize'}} for i, w in enumerate(widths)]
    return reqs


def pick_list(sid, i, values, message):
    """A drop-down on column i: the codebook (values None) or a fixed list."""
    cond = ({'type': 'ONE_OF_RANGE', 'values': [{'userEnteredValue': f"='{CODEBOOK}'!$A$2:$A"}]} if values is None else
            {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': v} for v in values]})
    return {'setDataValidation': {'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': ROWS, 'startColumnIndex': i, 'endColumnIndex': i + 1},
                                  'rule': {'condition': cond, 'strict': values is not None, 'showCustomUi': True, 'inputMessage': message}}}


def setup(sh, codebook, sessions):
    """Make the Codebook tab and any missing session tabs, and keep the drop-downs pointed at the codebook."""
    tabs = sh.tabs()
    titles = [CODEBOOK] + [t for ga in sessions for t in (tab(ga), law_tab(ga))]
    reqs = [{'addSheet': {'properties': {'title': t, 'gridProperties': {'frozenRowCount': 1}}}} for t in titles if t not in tabs]
    if reqs:
        for r in sh.batch(reqs).get('replies', []):
            p = r['addSheet']['properties']
            tabs[p['title']] = p['sheetId']
    # codebook: rewritten every time, so it follows the subtopic index
    sh.clear(f"'{CODEBOOK}'!A:B")
    sh.write([(f"'{CODEBOOK}'!A1", [['Code and subtopic', 'Major topic']] + [[text(f'{c} {lab}'), maj] for c, lab, maj in codebook])])
    reqs = []
    for title, cols, widths, wrap, lists in [(t, *spec) for ga in sessions for t, spec in (
            (tab(ga), (COLS, WIDTHS, ['What the bill does now'], {'Subtopic': None})),
            (law_tab(ga), (LAW_COLS, LAW_WIDTHS, ['What the law does', 'Rule for this code'], {'New code': None, 'Decision': list(DECISIONS)})))]:
        sid = tabs[title]
        pos, _ = read_tab(sh, title)
        if not pos:   # a new tab: header and layout
            sh.write([(f"'{title}'!A1", [cols])])
            pos = {h: i for i, h in enumerate(cols)}
            reqs += header_format(sid, cols, widths, wrap)
        for name, values in lists.items():
            if name in pos:
                reqs.append(pick_list(sid, pos[name], values, 'Pick a subtopic: type a code or words to search the list.' if values is None else
                                      'Correct, Change code (then pick a New code), or Unsure (say why in Notes).'))
    sh.batch(reqs)


def text(v, limit=4000):
    """A value for USER_ENTERED cells that Sheets keeps as typed (no formulas or dates from synopsis text)."""
    v = str(v or '')
    v = v if len(v) <= limit else v[:limit - 1] + '…'
    return "'" + v if v[:1] and v[0] in '=+-@' else v


def line(pos, row):
    out = [''] * (max(pos.values()) + 1)
    for name, v in row.items():
        if name in pos:
            out[pos[name]] = v
    return out


def link(url):
    return f'=HYPERLINK("{url}","Status")' if url else ''


def push(sh):
    qf = ROOT / CFG['build']['output'] / 'coding_queue.json'
    if not qf.exists():
        raise SystemExit('No build/coding_queue.json: run the check (pipeline/build.py) first.')
    queue = json.loads(qf.read_text())
    names = {c: lab for c, lab, _ in queue['codebook']}
    sessions = [int(g) for g in queue['sessions']]
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    setup(sh, queue['codebook'], sessions)
    today = dt.date.today().isoformat()
    for ga in sessions:
        q, seen = queue['sessions'][str(ga)], state.get(str(ga))
        first = seen is None   # first run for this session: the backlog of rewritten bills that didn't become law
        seen = seen or {}
        pos, rows = read_tab(sh, tab(ga))
        where = {cell(r, pos, 'Bill').strip(): i + 2 for i, r in enumerate(rows) if cell(r, pos, 'Bill').strip()}
        new, updates, again = [], [], 0
        for bill in sorted(q, key=bill_order):
            x = q[bill]
            before = seen.get(bill)
            why = None
            if bill in where:
                if before is not None and x['rw'] > before:   # rewritten again since it went into the sheet
                    r = rows[where[bill] - 2]
                    old = cell(r, pos, 'Subtopic') or cell(r, pos, 'Code before')
                    again += 1
                    for name, v in (('Why', f'Rewritten again {today}'), ('What the bill does now', text(x['now'])), ('Code before', text(old)), ('Subtopic', '')):
                        if name in pos:
                            updates.append((f"'{tab(ga)}'!{col(pos[name])}{where[bill]}", [[v]]))
            elif first:
                if x['code'] is None:
                    why = 'New bill'
                elif x['rw'] and (x['shell'] or not x['law']):
                    why = 'Placeholder given content' if x['shell'] else 'Rewritten by amendment'
            elif x['code'] is None and before is None:
                why = 'New bill'
            elif x['rw'] > (before or 0):
                why = 'Placeholder given content' if x['shell'] else 'Rewritten by amendment'
            if why:
                new.append(line(pos, {'Bill': bill, 'Why': why, 'Added': today, 'Short description': text(x['short']), 'What the bill does now': text(x['now']),
                                      'Code before': text(label(x['code'], names)), 'ILGA': link(x['url'])}))
            seen[bill] = x['rw']
        if new:
            sh.append(f"'{tab(ga)}'!A1", new)
        if updates:
            sh.write(updates)
        state[str(ga)] = seen
        print(f'{tab(ga)}: added {len(new):,} bill{"s" if len(new) != 1 else ""} to the coding sheet'
              + (f'; {again:,} already there were rewritten again' if again else '') + '.')
        push_laws(sh, ga, queue['laws'].get(str(ga), {}), names, queue.get('rules', {}))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def push_laws(sh, ga, laws, names, rules):
    """Add laws not yet in the laws tab, grouped by code so a reviewer can work through one subtopic at a time. A row
    nobody has decided on yet follows the law's current code (e.g. after the bill tab recoded it)."""
    title = law_tab(ga)
    pos, rows = read_tab(sh, title)
    where = {cell(r, pos, 'Bill').strip(): (i + 2, r) for i, r in enumerate(rows) if cell(r, pos, 'Bill').strip()}
    new, updates = [], []
    order = sorted(laws, key=lambda b: (laws[b]['code'] if laws[b]['code'] is not None else 10 ** 6, bill_order(b)))
    for bill in order:
        x = laws[bill]
        code = label(x['code'], names)
        rule = rules.get(str(x['code']), '')
        if bill not in where:
            new.append(line(pos, {'Bill': bill, 'Public Act': x['pa'], 'Short description': text(x['short']), 'What the law does': text(x['now']),
                                  'Code': text(code), 'Rule for this code': text(rule), 'ILGA': link(x['url'])}))
            continue
        n, r = where[bill]
        if not cell(r, pos, 'Decision').strip() and not cell(r, pos, 'New code').strip() and parse_code(cell(r, pos, 'Code')) != parse_code(code):
            for name, v in (('Code', text(code)), ('Rule for this code', text(rule))):
                if name in pos:
                    updates.append((f"'{title}'!{col(pos[name])}{n}", [[v]]))
    if new:
        sh.append(f"'{title}'!A1", new)
    if updates:
        sh.write(updates)
    print(f'{title}: added {len(new):,} law{"s" if len(new) != 1 else ""}' + (f'; updated the code of {len(updates) // 2:,} not yet reviewed' if updates else '') + '.')


def main(argv=None):
    cmd = (argv or sys.argv[1:] or [''])[0]
    if cmd not in ('pull', 'push'):
        raise SystemExit(__doc__)
    sh = connect()
    if sh:
        pull(sh) if cmd == 'pull' else push(sh)


if __name__ == '__main__':
    main()
