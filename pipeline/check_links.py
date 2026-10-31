#!/usr/bin/env python3
"""Keep the think tank reports' links working: check them, repair dead PDF links, make sure the Internet Archive has a
copy of each, and keep a private backup of every PDF in Dropbox.

    python pipeline/check_links.py              a pass if the last one is a week old (or unfinished), else nothing
    python pipeline/check_links.py --force      a pass now
    options: --every DAYS (7), --minutes N (time budget, 50), --save N (new Internet Archive captures per run, 60)

For every report page and PDF link in the reports file ([thinktanks] file in config.toml):
  1. Check it, as the project's report collector (same user agent). A 404/410, a dead host, or a "PDF" link that
     returns something else counts as dead; a site that refuses automated requests (401/403/429) is "blocked", which is
     not treated as dead.
  2. A dead PDF whose report page still works is looked for again on that page (the organization's own PDF). So is
     the PDF of a report whose PDF link is shared by three or more reports (an organization-wide document such as a
     prospectus that was picked up from every page, not the report itself).
  3. Each link gets an Internet Archive (Wayback Machine) copy: an existing capture, or a new one requested through
     Save Page Now for links that work but have none yet (a few dozen per run; the Archive limits the rate).
  4. Each report's PDF is saved to Dropbox, IAP/Think tank PDFs/<organization>/<id> <title>.pdf, from the original,
     the repaired link or the archived copy. These copies are for the project's own use and are never published.

What it learns goes in inputs/thinktanks/links.json, which the check reads: the site links to the original while it
works, and to the repaired link or the archived copy when it doesn't (marked "archived copy"). The reports file itself
is never changed. A pass can take several runs (it stops at --minutes and picks up where it left off).
"""
import argparse, datetime as dt, json, os, re, sys, threading, time, tomllib, urllib.error, urllib.parse, urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

import webget
from fetch_reports import UA, page_meta

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
CFG = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
STATE = DATA / 'inputs' / 'thinktanks' / 'links.json'
BACKUP = 'Think tank PDFs'          # folder next to Website/ in the IAP folder
WAYBACK = 'https://archive.org/wayback/available?url='
PAUSE = 0.5                          # between requests to the same site
NOW = lambda: dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def age_days(stamp):
    if not stamp:
        return 1e9
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)).total_seconds() / 86400


def reports():
    """[(id, org, title, page url, pdf url)] from the main reports file."""
    tt = CFG['thinktanks']
    c = tt['columns']
    df = pd.read_excel(DATA / tt['file'])
    out = []
    for r in df.to_dict('records'):
        clean = lambda v: str(v).strip() if isinstance(v, str) and str(v).strip().startswith('http') else ''
        out.append((int(r[c['id']]), str(r[c['org']] or '').strip(), str(r[c['title']] or '').strip(), clean(r.get(c['url'])), clean(r.get(c['pdf']))))
    return out


def probe(url, pdf):
    """'ok', 'dead', 'blocked' or 'error'."""
    try:
        body, ctype, _ = webget.get(url, UA, timeout=30, limit=1024)
    except urllib.error.HTTPError as e:
        return 'dead' if e.code in (404, 410) else 'blocked' if e.code in (401, 403, 429) else 'error'
    except urllib.error.URLError as e:
        reason = str(getattr(e, 'reason', e))
        return 'dead' if any(k in reason for k in ('Name or service not known', 'nodename nor servname', 'No address associated', 'getaddrinfo failed')) else 'error'
    except Exception:  # noqa: BLE001 - timeouts, resets
        return 'error'
    if pdf and not body.startswith(b'%PDF') and 'pdf' not in ctype:
        return 'dead'   # e.g. the site now sends its home page or a "not found" page instead of the file
    return 'ok'


def by_host(urls, work, budget):
    """Run work(url) for each url, one site at a time per thread (with a pause), several sites at once."""
    groups = defaultdict(list)
    for u in urls:
        groups[urlparse(u).hostname or ''].append(u)

    def run(host_urls):
        for u in host_urls:
            if time.time() > budget:
                return
            work(u)
            time.sleep(PAUSE)
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(run, groups.values()))


def wayback(url):
    """The newest Internet Archive capture of url ('' if none), as an https link."""
    try:
        with urllib.request.urlopen(urllib.request.Request(WAYBACK + urllib.parse.quote(url, safe=''), headers={'User-Agent': UA}), timeout=40) as r:
            snap = json.loads(r.read()).get('archived_snapshots', {}).get('closest') or {}
    except Exception:  # noqa: BLE001
        return None   # unknown: try again next run
    if not snap.get('available') or str(snap.get('status', '200'))[:1] not in '23':
        return ''
    return re.sub(r'^http://', 'https://', snap['url'])


def save_now(url):
    """Ask the Internet Archive to capture url. True if it accepted."""
    try:
        with urllib.request.urlopen(urllib.request.Request('https://web.archive.org/save/' + url, headers={'User-Agent': UA}), timeout=180) as r:
            return r.status < 400
    except Exception:  # noqa: BLE001
        return False


def raw(archive_url):
    """The archived file itself, without the Wayback toolbar (…/web/<time>id_/<url>)."""
    return re.sub(r'/web/(\d+)/', r'/web/\1id_/', archive_url, count=1)


class Backup:
    """Saves PDFs to IAP/Think tank PDFs in Dropbox: through the Dropbox API on GitHub, or straight into the synced
    Dropbox folder on a lab computer."""

    def __init__(self):
        self.api = self.local = None
        if os.environ.get('DROPBOX_REFRESH_TOKEN') or os.environ.get('DROPBOX_TOKEN'):
            import dropbox_sync
            dropbox_sync.TOKEN = dropbox_sync.token()
            self.api = dropbox_sync
        elif (DATA.parent / 'Bills').exists():   # the real IAP folder, synced by Dropbox
            self.local = DATA.parent / BACKUP

    @property
    def on(self):
        return bool(self.api or self.local)

    def save(self, rel, data):
        if self.local:
            f = self.local / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                f.write_bytes(data)
            return
        path = f'{self.api.IAP}/{BACKUP}/{rel}'
        try:
            self.api.upload(path, data, {'.tag': 'add'})
        except self.api.DropboxError as e:
            if not (e.code == 409 and 'conflict' in e.detail):   # already there
                raise


def shared_pdfs(rows):
    """PDF links used by three or more reports: an organization-wide document (a prospectus, an annual report) that the
    report collector picked up from every page, not any one report's PDF. The check leaves them off the site too."""
    n = Counter(p for *_, p in rows if p)
    return {p for p, k in n.items() if k >= 3}


def page_pdfs(body, url):
    """The organization's own PDF links on a page, in page order."""
    h = body.decode('utf-8', 'replace')
    site = '.'.join((urlparse(url).hostname or '').split('.')[-2:])
    out = []
    for href in re.findall(r'href=["\']([^"\']+\.pdf)(?:[?#][^"\']*)?["\']', h, re.I):
        u = urllib.parse.urljoin(url, href)
        if (urlparse(u).hostname or '').endswith(site) and u not in out:
            out.append(u)
    return out


SOCIAL = re.compile(r'(^|\.)(facebook|twitter|x|linkedin|instagram|youtube|youtu|vimeo|flickr|tiktok|threads|bsky|pinterest|google|'
                    r'googleapis|gstatic|apple|feedburner|eepurl|list-manage|paypal|donorbox|addthis|sharethis|wordpress|gravatar)\.[a-z]+$')
VIDEO = re.compile(r'^https?://(www\.)?(vimeo\.com/\d+|youtube\.com/watch|youtu\.be/)')


def page_elsewhere(body, url):
    """Links on a page to the publication somewhere else (a journal, a book, a video), in page order: the page of a
    report the organization wrote for another publisher. Social media and sharing links are left out, except a video."""
    h = body.decode('utf-8', 'replace')
    site = '.'.join((urlparse(url).hostname or '').split('.')[-2:])
    out = []
    for href in re.findall(r'<a\b[^>]*?\shref=["\'](https?://[^"\']+)["\']', h, re.I):
        u = href.replace('&amp;', '&')
        host = (urlparse(u).hostname or '').lower()
        if host.endswith(site) or re.search(r'\.(png|jpe?g|gif|svg|ico|css|js)(\?|$)', u, re.I):
            continue
        if SOCIAL.search(host) and not VIDEO.match(u):
            continue
        if u not in out:
            out.append(u)
    return out


def filename(rid, org, title):
    clean = lambda s, n: re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|#%{}~&]+', ' ', s)).strip()[:n].rstrip(' .')
    return f'{clean(org, 80) or "Unknown"}/{rid} {clean(title, 90) or "report"}.pdf'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--every', type=float, default=7)
    ap.add_argument('--minutes', type=float, default=50)
    ap.add_argument('--save', type=int, default=60)
    a = ap.parse_args(argv)
    budget = time.time() + a.minutes * 60
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    links = st.setdefault('links', {})
    rows = reports()
    shared = shared_pdfs(rows)
    fix = st.setdefault('fix', {})       # report id -> {'p': its own PDF found on its page, 't': when looked}
    eff = lambda r: (fix.get(str(r[0]), {}).get('p', '') if r[4] in shared else r[4])
    pages = {u for _, _, _, u, _ in rows if u}
    pdfs = {eff(r) for r in rows if eff(r)}
    due = [u for u in pages | pdfs if age_days(links.get(u, {}).get('t')) >= a.every]
    pending_archive = [u for u in pages | pdfs if links.get(u, {}).get('a') is None]
    pending_backup = [r for r in rows if eff(r) and not links.get(eff(r), {}).get('b')]
    redo = lambda rid: age_days(fix.get(str(rid), {}).get('t')) >= 30 or not fix[str(rid)].get('p') and 'o' not in fix[str(rid)]
    pending_fix = [r for r in rows if r[4] in shared and redo(r[0])]
    if not a.force and not due and not pending_archive and not pending_backup and not pending_fix:
        print(f'Report links were checked {age_days(st.get("pass")):.0f} days ago; next check in {max(0, a.every - age_days(st.get("pass"))):.0f}.')
        return 0
    if a.force:
        due = sorted(pages | pdfs)
    lock = threading.Lock()
    saved = [0]

    def keep():
        with lock:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_suffix('.tmp')
            tmp.write_text(json.dumps(st, indent=0, sort_keys=True))
            tmp.replace(STATE)

    # 1. check
    print(f'Checking {len(due):,} links…', flush=True)
    done = [0]

    def check(u):
        s = probe(u, u in pdfs)
        with lock:
            e = links.setdefault(u, {})
            e['s'], e['t'] = s, NOW()
            done[0] += 1
        if done[0] % 200 == 0:
            keep()
    by_host(due, check, budget)
    keep()

    # 2. repair dead PDFs from their report page
    repaired = 0
    for rid, org, title, page, pdf in rows:
        if time.time() > budget:
            break
        e = links.get(pdf)
        if not pdf or not e or e.get('s') != 'dead' or links.get(page, {}).get('s') != 'ok' or e.get('ft') and age_days(e['ft']) < a.every:
            continue
        e['ft'] = NOW()
        e.pop('f', None)
        try:
            body, ctype, final = webget.get(page, UA, timeout=30)
            found = page_meta(body, final)[3] if 'html' in ctype else ''
        except Exception:  # noqa: BLE001
            found = ''
        if found and found != pdf and probe(found, True) == 'ok':
            e['f'] = found
            repaired += 1
        time.sleep(PAUSE)
    keep()

    # 2b. reports whose PDF link is an organization-wide document shared by several reports: find their own PDF
    for rid, org, title, page, pdf in rows:
        if time.time() > budget:
            break
        if pdf not in shared or not page or not redo(rid):
            continue
        found = other = ''
        try:
            body, ctype, final = webget.get(page, UA, timeout=30)
            html = 'html' in ctype
            for cand in page_pdfs(body, final) if html else []:
                if cand not in shared and probe(cand, True) == 'ok':
                    found = cand
                    break
            # no PDF of its own: the report was published elsewhere; link to it there (a PDF there if there is one)
            elsewhere = page_elsewhere(body, final) if html and not found else []
            for cand in sorted(elsewhere, key=lambda u: not re.search(r'\.pdf(\?|$)', u, re.I)):
                st_ = probe(cand, cand.lower().split('?')[0].endswith('.pdf'))
                doi = re.search(r'\b10\.\d{4,9}/[^\s&?#"\']+', urllib.parse.unquote(cand))
                if st_ == 'dead' and doi:   # a publisher site that has closed: the DOI still leads to the article
                    cand = 'https://doi.org/' + doi.group(0)
                    st_ = probe(cand, False)
                if st_ in ('ok', 'blocked'):
                    if st_ == 'ok' and cand.lower().split('?')[0].endswith('.pdf'):
                        found = cand
                    else:
                        other = cand
                    break
        except Exception:  # noqa: BLE001
            pass
        fix[str(rid)] = {'p': found, 'o': other, 't': NOW()}
        if found:
            repaired += 1
            pdfs.add(found)
            links.setdefault(found, {}).update(s='ok', t=NOW())
        time.sleep(PAUSE)
    keep()

    # 3. Internet Archive copies: look up captures; ask for new ones (a few per run) for working links with none
    look = [u for u in sorted(pages | pdfs) if links.get(u, {}).get('a') is None or (links[u]['a'] == '' and age_days(links[u].get('at')) >= 30)]
    print(f'Looking up Internet Archive copies of {len(look):,} links…', flush=True)
    # while PDFs still need a backup, the lookups get half the time left, so a long first pass makes progress on both
    stop = time.time() + (budget - time.time()) / 2 if pending_backup else budget
    for i, u in enumerate(look):
        if time.time() > stop:
            break
        e = links.setdefault(u, {})
        got = wayback(u)
        if got is None:
            continue
        e['a'], e['at'] = got, NOW()
        if not got and e.get('s') in ('ok', 'blocked') and saved[0] < a.save and age_days(e.get('sv')) > 30:
            if save_now(u):
                saved[0] += 1
                e['sv'] = NOW()
                e.pop('a', None)   # look it up again next run, once the capture is indexed
            time.sleep(10)
        else:
            time.sleep(1)
        if i % 100 == 99:
            keep()
    keep()

    # 4. private PDF backups in Dropbox
    bk = Backup()
    backed = 0
    if not bk.on:
        print('PDF backups skipped: no Dropbox connection here (they are made on GitHub, or on a computer with the IAP folder).')
    else:
        for r in rows:
            rid, org, title, page, pdf = r[0], r[1], r[2], r[3], eff(r)
            if time.time() > budget:
                break
            e = links.get(pdf) if pdf else None
            if not e or e.get('b'):
                continue
            sources = [x for x in (pdf if e.get('s') == 'ok' else '', e.get('f', ''), raw(e['a']) if e.get('a') else '') if x]
            for src in sources:
                try:
                    data, ctype, _ = webget.get(src, UA, timeout=120)
                except Exception:  # noqa: BLE001
                    continue
                if data.startswith(b'%PDF'):
                    rel = filename(rid, org, title)
                    bk.save(rel, data)
                    e['b'] = f'{BACKUP}/{rel}'
                    backed += 1
                    break
            time.sleep(PAUSE)
            if backed and backed % 25 == 0:
                keep()
        keep()

    finished = time.time() <= budget
    if finished:
        st['pass'] = NOW()
        keep()
    # summary
    s = Counter(links.get(u, {}).get('s', 'unchecked') for u in pages | pdfs)
    dead = [u for u in pages | pdfs if links.get(u, {}).get('s') == 'dead']
    fixed = [u for u in dead if links[u].get('f') or links[u].get('a')]
    print(f'\nLinks: {s["ok"]:,} working, {s["dead"]:,} dead, {s["blocked"]:,} refused by the site (not counted as dead), '
          f'{s["error"]:,} did not answer, {s["unchecked"]:,} not checked yet.')
    print(f'Dead links with a replacement: {len(fixed):,} of {len(dead):,} ({sum(1 for u in dead if links[u].get("f")):,} repaired from the report page, '
          f'the rest an Internet Archive copy). Repaired this run: {repaired:,}.')
    print(f'Internet Archive: {sum(1 for u in pages | pdfs if links.get(u, {}).get("a")):,} of {len(pages | pdfs):,} links have a copy; '
          f'{saved[0]:,} new captures requested this run.')
    if shared:
        own = sum(1 for r in rows if r[4] in shared and fix.get(str(r[0]), {}).get('p'))
        other = sum(1 for r in rows if r[4] in shared and not fix.get(str(r[0]), {}).get('p') and fix.get(str(r[0]), {}).get('o'))
        print(f'Reports whose PDF link was an organization-wide document: {sum(1 for r in rows if r[4] in shared):,}; their own PDF found for {own:,}, '
              f'a link to the publication elsewhere for {other:,}.')
    print(f'PDF backups in Dropbox: {sum(1 for p in pdfs if links.get(p, {}).get("b")):,} of {len(pdfs):,}; {backed:,} saved this run.')
    if not finished:
        print(f'\nStopped after {a.minutes:g} minutes; the next run continues.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
