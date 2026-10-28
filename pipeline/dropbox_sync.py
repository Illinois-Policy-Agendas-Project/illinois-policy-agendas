#!/usr/bin/env python3
"""Copy the project's data between Dropbox and this computer, for running the update on GitHub (.github/workflows/update.yml).

    python pipeline/dropbox_sync.py pull    download what the build reads into IL_AGENDAS_DATA (IAP/Website and the files
                                            config.toml points to outside it, such as ../Bills/... and ../SOTS/...)
    python pipeline/dropbox_sync.py push    upload what the run wrote: files under Website/inputs and Website/state that are
                                            new or changed (bill status from ILGA, new think tank reports, worksheets,
                                            the publish log)

A file someone changed in Dropbox while the run was going is never overwritten: Dropbox refuses the upload because the
file's version moved on, and the run reports it. Only the Python standard library is used.

Settings (GitHub secrets, or environment variables when run by hand):
    DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN   from pipeline/dropbox_auth.py (see the README)
    DROPBOX_IAP_PATH     the IAP folder in Dropbox (default /Academic/UIC/Research/IAP)
    IL_AGENDAS_DATA      where IAP/Website goes on this computer
"""
import hashlib, json, os, posixpath, sys, time, tomllib, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get('DROPBOX_API_BASE', 'https://api.dropboxapi.com')            # changeable for tests
CONTENT = os.environ.get('DROPBOX_CONTENT_BASE', 'https://content.dropboxapi.com')
IAP = os.environ.get('DROPBOX_IAP_PATH', '/Academic/UIC/Research/IAP').rstrip('/')
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or 'iap/Website').expanduser().resolve()
BASE = DATA.parent                       # local copy of IAP/
REVS = BASE / '.dropbox_revs.json'       # what was downloaded: Dropbox path -> {rev, hash}, so push knows what changed
FOLDERS = ['Website/inputs', 'Website/state']


def token():
    if os.environ.get('DROPBOX_TOKEN'):  # tests
        return os.environ['DROPBOX_TOKEN']
    missing = [k for k in ('DROPBOX_APP_KEY', 'DROPBOX_APP_SECRET', 'DROPBOX_REFRESH_TOKEN') if not os.environ.get(k)]
    if missing:
        raise SystemExit(f'Missing settings: {", ".join(missing)}. Add them as secrets in the GitHub repository (see the README).')
    body = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': os.environ['DROPBOX_REFRESH_TOKEN'],
                                   'client_id': os.environ['DROPBOX_APP_KEY'], 'client_secret': os.environ['DROPBOX_APP_SECRET']}).encode()
    with urllib.request.urlopen(urllib.request.Request(f'{API}/oauth2/token', data=body), timeout=60) as r:
        return json.loads(r.read())['access_token']


TOKEN = None


def request(url, data, headers):
    """One Dropbox request, retried when Dropbox asks to slow down. Returns (body bytes, response headers)."""
    headers = {'Authorization': f'Bearer {TOKEN}', **headers}
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method='POST'), timeout=300) as r:
                return r.read(), r.headers
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(int(e.headers.get('Retry-After') or 2 ** attempt))
                continue
            raise DropboxError(e.code, e.read().decode('utf-8', 'replace')) from None
    raise DropboxError(0, 'gave up after retries')


def rpc(endpoint, body):
    out, _ = request(f'{API}/2/{endpoint}', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    return json.loads(out)


def fetch(path):
    """(file bytes, metadata) of a Dropbox file."""
    out, h = request(f'{CONTENT}/2/files/download', b'', {'Dropbox-API-Arg': json.dumps({'path': path}, ensure_ascii=True)})
    return out, json.loads(h['Dropbox-API-Result'])


def upload(path, data, mode):
    out, _ = request(f'{CONTENT}/2/files/upload', data, {'Content-Type': 'application/octet-stream',
                     'Dropbox-API-Arg': json.dumps({'path': path, 'mode': mode, 'autorename': False, 'mute': True}, ensure_ascii=True)})
    return json.loads(out)


class DropboxError(Exception):
    def __init__(self, code, detail):
        super().__init__(f'{code}: {detail[:300]}')
        self.code, self.detail = code, detail


def content_hash(path):
    """Dropbox's content hash: SHA-256 of the SHA-256 of each 4 MB block."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while (block := f.read(4 * 1024 * 1024)):
            h.update(hashlib.sha256(block).digest())
    return h.hexdigest()


def local(dbx_path):
    rel = posixpath.relpath(dbx_path, IAP)
    return BASE / rel


def listing(folder):
    """Every file under a Dropbox folder: [(path_display, rev, content_hash)]; empty if the folder doesn't exist yet."""
    out = []
    try:
        res = rpc('files/list_folder', {'path': folder, 'recursive': True})
    except DropboxError as e:
        if 'not_found' in e.detail:
            return []
        raise
    while True:
        out += [(x['path_display'], x['rev'], x['content_hash']) for x in res['entries'] if x['.tag'] == 'file']
        if not res.get('has_more'):
            return out
        res = rpc('files/list_folder/continue', {'cursor': res['cursor']})


def wanted_files():
    """Files the build reads that live outside Website/inputs, from config.toml (paths relative to IAP/Website)."""
    cfg = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
    rels = [cfg['codebook']['file'], cfg['governors']['file'], cfg['sots']['file'], cfg['thinktanks']['file']]
    rels += [s['details'] for s in cfg['bills']['sessions']]
    out = []
    for rel in rels:
        p = posixpath.normpath(posixpath.join(IAP + '/Website', rel))
        if not p.startswith(IAP + '/Website/inputs/'):
            out.append(p)
    return out


def download(path, revs):
    data, meta = fetch(path)
    dest = local(meta['path_display'])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    revs[meta['path_lower']] = {'rev': meta['rev'], 'hash': meta['content_hash']}
    return len(data)


def pull():
    revs, n, size = {}, 0, 0
    for folder in FOLDERS:
        for path, _, _ in listing(f'{IAP}/{folder}'):
            size += download(path, revs)
            n += 1
    for path in wanted_files():
        try:
            size += download(path, revs)
            n += 1
        except DropboxError as e:
            if 'not_found' not in e.detail:
                raise
            print(f'  not in Dropbox (yet): {path}')
    DATA.mkdir(parents=True, exist_ok=True)
    REVS.write_text(json.dumps(revs, indent=0))
    print(f'Downloaded {n} files ({size / 1e6:.1f} MB) from Dropbox into {BASE}.')


def push():
    revs = json.loads(REVS.read_text()) if REVS.exists() else {}
    sent, kept = [], []
    for folder in FOLDERS:
        root = BASE / folder
        if not root.exists():
            continue
        for f in sorted(p for p in root.rglob('*') if p.is_file() and not p.name.startswith(('.', '~$')) and p.suffix != '.tmp'):
            dbx = f'{IAP}/{f.relative_to(BASE).as_posix()}'
            known = revs.get(dbx.lower())
            h = content_hash(f)
            if known and known['hash'] == h:
                continue
            mode = {'.tag': 'update', 'update': known['rev']} if known else {'.tag': 'add'}
            try:
                res = upload(dbx, f.read_bytes(), mode)
                revs[dbx.lower()] = {'rev': res['rev'], 'hash': res['content_hash']}
                sent.append(f.relative_to(BASE).as_posix())
            except DropboxError as e:
                if e.code == 409 and 'conflict' in e.detail:
                    kept.append(f.relative_to(BASE).as_posix())
                else:
                    raise
    REVS.write_text(json.dumps(revs, indent=0))
    print(f'Uploaded {len(sent)} file(s) to Dropbox' + (':' if sent else '.'), *sent, sep='\n  ')
    if kept:
        print(f'Not uploaded, because someone changed them in Dropbox during the run (their version is kept): {", ".join(kept)}')


def main(argv=None):
    global TOKEN
    cmd = (argv or sys.argv[1:] or [''])[0]
    if cmd not in ('pull', 'push'):
        raise SystemExit(__doc__)
    TOKEN = token()
    pull() if cmd == 'pull' else push()


if __name__ == '__main__':
    main()
