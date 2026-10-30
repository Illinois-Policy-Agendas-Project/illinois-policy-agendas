#!/usr/bin/env python3
"""The Google coding sheet: where coders code new bills and bills an amendment rewrote.

    python pipeline/coding_sheet.py pull    before the check: copy the sheet's codes to inputs/coding_sheet/<ga>.csv,
                                            which the check reads (codes there override the bill file and coding files)
    python pipeline/coding_sheet.py push    after the check: add to the sheet every bill introduced or rewritten since
                                            the last run, from build/coding_queue.json

The sheet has one tab per session ("104th GA") and a Codebook tab that feeds the Subtopic column's list. Each bill goes
in once when it is introduced, and again whenever an amendment replaces everything after the enacting clause (unless it
only reinserts the introduced bill): its row is updated, the earlier code moves to "Code before" and Subtopic is cleared.
Rows coders delete don't come back. What has been added is remembered in state/coding_sheet.json.

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
CODEBOOK = 'Codebook'
ROWS = 5000   # rows of each session tab that get the Subtopic list


def tab(ga):
    return f'{ga}th GA'


def col(i):
    return chr(ord('A') + i)


def label(code, names):
    """How a code appears in the sheet's list: "1201 Criminal Code"."""
    return '' if code is None else f'{code} {names.get(code, "")}'.strip()


def parse_code(v):
    """The code at the start of a Subtopic cell ("1201 Criminal Code", "1201", 1201.0), or the text as is."""
    v = str(v or '').strip()
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


def read_tab(sh, ga):
    """(header positions {column name: index}, [row lists]) of a session tab; header stays as coders arranged it."""
    rows = sh.values(f"'{tab(ga)}'!A1:Z")
    if not rows:
        return {}, []
    pos = {h.strip(): i for i, h in enumerate(rows[0]) if str(h).strip()}
    return pos, rows[1:]


def cell(row, pos, name):
    i = pos.get(name)
    return row[i] if i is not None and i < len(row) else ''


def pull(sh):
    tabs = sh.tabs()
    for ga in live_sessions():
        if tab(ga) not in tabs:
            continue
        pos, rows = read_tab(sh, ga)
        if 'Bill' not in pos or 'Subtopic' not in pos:
            raise SystemExit(f'The "{tab(ga)}" tab needs the columns Bill and Subtopic in its first row.')
        out = [{'bill': cell(r, pos, 'Bill').strip(), 'subtopic': parse_code(cell(r, pos, 'Subtopic')), 'coder': cell(r, pos, 'Coder'),
                'notes': cell(r, pos, 'Notes'), 'why': cell(r, pos, 'Why'), 'added': cell(r, pos, 'Added')} for r in rows]
        out = [r for r in out if r['bill']]
        f = DATA / 'inputs' / 'coding_sheet' / f'{ga}.csv'
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open('w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=['bill', 'subtopic', 'coder', 'notes', 'why', 'added'])
            w.writeheader()
            w.writerows(out)
        done = sum(1 for r in out if r['subtopic'])
        print(f'{tab(ga)}: {len(out):,} bills in the sheet, {done:,} coded. Saved to {f}.')


def setup(sh, codebook, sessions):
    """Make the Codebook tab and any missing session tab, and keep the Subtopic list pointed at the codebook."""
    tabs = sh.tabs()
    reqs = []
    for title in [CODEBOOK] + [tab(ga) for ga in sessions]:
        if title not in tabs:
            reqs.append({'addSheet': {'properties': {'title': title, 'gridProperties': {'frozenRowCount': 1}}}})
    if reqs:
        for r in sh.batch(reqs).get('replies', []):
            p = r['addSheet']['properties']
            tabs[p['title']] = p['sheetId']
    # codebook: rewritten every time, so it follows the subtopic index
    sh.clear(f"'{CODEBOOK}'!A:B")
    sh.write([(f"'{CODEBOOK}'!A1", [['Code and subtopic', 'Major topic']] + [[text(f'{c} {lab}'), maj] for c, lab, maj in codebook])])
    reqs = []
    for ga in sessions:
        sid = tabs[tab(ga)]
        pos, rows = read_tab(sh, ga)
        if not pos:   # a new tab: header and layout
            sh.write([(f"'{tab(ga)}'!A1", [COLS])])
            pos = {h: i for i, h in enumerate(COLS)}
            reqs += [{'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                                     'cell': {'userEnteredFormat': {'textFormat': {'bold': True}, 'backgroundColor': {'red': .93, 'green': .95, 'blue': .98}}},
                                     'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
                     {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 1, 'startColumnIndex': COLS.index('What the bill does now'),
                                               'endColumnIndex': COLS.index('What the bill does now') + 1},
                                     'cell': {'userEnteredFormat': {'wrapStrategy': 'CLIP', 'verticalAlignment': 'TOP'}}, 'fields': 'userEnteredFormat(wrapStrategy,verticalAlignment)'}},
                     {'setBasicFilter': {'filter': {'range': {'sheetId': sid}}}}]
            reqs += [{'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': i, 'endIndex': i + 1},
                                                    'properties': {'pixelSize': w}, 'fields': 'pixelSize'}} for i, w in enumerate(WIDTHS)]
        if 'Subtopic' in pos:
            i = pos['Subtopic']
            reqs.append({'setDataValidation': {'range': {'sheetId': sid, 'startRowIndex': 1, 'endRowIndex': ROWS, 'startColumnIndex': i, 'endColumnIndex': i + 1},
                                               'rule': {'condition': {'type': 'ONE_OF_RANGE', 'values': [{'userEnteredValue': f"='{CODEBOOK}'!$A$2:$A"}]},
                                                        'strict': False, 'showCustomUi': True,
                                                        'inputMessage': 'Pick a subtopic: type a code or words to search the list.'}}})
    sh.batch(reqs)


def text(v, limit=4000):
    """A value for USER_ENTERED cells that Sheets keeps as typed (no formulas or dates from synopsis text)."""
    v = str(v or '')
    v = v if len(v) <= limit else v[:limit - 1] + '…'
    return "'" + v if v[:1] in '=+-@' else v


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
        pos, rows = read_tab(sh, ga)
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
                row = {'Bill': bill, 'Why': why, 'Added': today, 'Short description': text(x['short']), 'What the bill does now': text(x['now']),
                       'Code before': text(label(x['code'], names)), 'Subtopic': '', 'Coder': '', 'Notes': '',
                       'ILGA': f'=HYPERLINK("{x["url"]}","Status")' if x['url'] else ''}
                width = max(pos.values()) + 1
                line = [''] * width
                for name, v in row.items():
                    if name in pos:
                        line[pos[name]] = v
                new.append(line)
            seen[bill] = x['rw']
        if new:
            sh.append(f"'{tab(ga)}'!A1", new)
        if updates:
            sh.write(updates)
        state[str(ga)] = seen
        print(f'{tab(ga)}: added {len(new):,} bill{"s" if len(new) != 1 else ""} to the coding sheet'
              + (f'; {again:,} already there were rewritten again' if again else '') + '.')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def main(argv=None):
    cmd = (argv or sys.argv[1:] or [''])[0]
    if cmd not in ('pull', 'push'):
        raise SystemExit(__doc__)
    sh = connect()
    if sh:
        pull(sh) if cmd == 'pull' else push(sh)


if __name__ == '__main__':
    main()
