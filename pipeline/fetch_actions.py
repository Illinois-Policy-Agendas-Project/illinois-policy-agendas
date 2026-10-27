#!/usr/bin/env python3
"""Download the status of every House and Senate bill from the General Assembly's public file repository.

    python pipeline/fetch_actions.py              update every session in config.toml that has an `actions` folder
    python pipeline/fetch_actions.py --ga 104     one session
    python pipeline/fetch_actions.py --force      download every bill again

The General Assembly publishes one Bill Status XML file per bill at https://ftp.ilga.gov/legislation/<ga>/BillStatus/XML/
with its short description, sponsors, synopsis and the date and chamber of every action; ILGA asks automated tools to
use that repository rather than ilga.gov. The repository is refreshed nightly.

For each session it writes, in the session's `actions` folder (in Dropbox, next to the coding files):
    bill_actions.csv   bill, seq, date (YYYY-MM-DD), chamber, action: one row per action, in the order ILGA lists them
    bill_status.csv    bill, shortdesc, sponsors, synopsis: one row per bill
    listing.json       when each bill's file last changed, so the next run downloads only bills that changed
The build uses these to bring the scraped bill file up to date (latest actions, sponsors, synopses and stages, and bills
introduced since the scrape) and to make the day-by-day session replay.
"""
import argparse, csv, html, json, os, re, sys, time, tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import webget  # downloads, with fixes for missing certificates

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
UA = 'Mozilla/5.0 (Illinois Policy Agendas Project (IPAP) bill status collector; research website)'
REPO = 'https://ftp.ilga.gov/legislation/{ga}/BillStatus/XML/'
COLS = ['bill', 'seq', 'date', 'chamber', 'action']
STATUS_COLS = ['bill', 'shortdesc', 'sponsors', 'synopsis']
# a line of the folder listing: "  8/16/2025  12:22 AM   5123 <A HREF="/legislation/104/BillStatus/XML/10400HB0022.xml">"
LISTED = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+\d+\s+<A HREF="([^"]+/(\d{3})00([A-Z]+)0*(\d+)\.xml)"', re.I)
ACTION = re.compile(r'<statusdate>(.*?)</statusdate>\s*<chamber>(.*?)</chamber>\s*<action>(.*?)</action>', re.S | re.I)


def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def iso(d):
    try:
        return datetime.strptime(d.strip(), '%m/%d/%Y').strftime('%Y-%m-%d')
    except ValueError:
        return ''


def listing(ga, types):
    """{bill: (url, stamp)} for the session's bills of the wanted types, e.g. {"HB 22": (url, "8/16/2025 12:22 AM")}."""
    body, _, url = webget.get(REPO.format(ga=ga), UA, timeout=120)
    out = {}
    for date, clock, href, g, t, n in LISTED.findall(body.decode('utf-8', 'replace')):
        if int(g) == ga and t.upper() in types:
            out[f'{t.upper()} {int(n)}'] = (urljoin(url, href), f'{date} {clock}')
    return out


def decode(body):
    """The files say their own encoding (ISO-8859-1 for most, UTF-8 for some)."""
    m = re.match(rb"<\?xml[^>]*encoding=['\"]([\w-]+)", body)
    try:
        return body.decode(m.group(1).decode() if m else 'utf-8')
    except (LookupError, UnicodeDecodeError):
        return body.decode('latin-1')


def status_of(xml):
    """A Bill Status file's short description, sponsors, synopsis (each version's text, joined by "; " as in the scraped
    bill files) and [(date, chamber, action)] from its <actions> block."""
    one = lambda tag: clean((re.search(rf'<{tag}>(.*?)</{tag}>', xml, re.S | re.I) or [None, ''])[1])
    syn = re.search(r'<synopsis>(.*?)</synopsis>', xml, re.S | re.I)
    texts = [clean(t) for t in re.findall(r'<SynopsisText>(.*?)</SynopsisText>', syn.group(1) if syn else '', re.S | re.I)]
    m = re.search(r'<actions>(.*?)</actions>', xml, re.S | re.I)
    return {'shortdesc': one('shortdesc'), 'sponsors': one('sponsors'), 'synopsis': '; '.join(t for t in texts if t),
            'actions': [(iso(d), clean(c), clean(a)) for d, c, a in ACTION.findall(m.group(1) if m else '')]}


def fetch_session(ga, folder, types, force=False, workers=4, pause=0.15):
    folder.mkdir(parents=True, exist_ok=True)
    csv_path, status_path, seen_path = folder / 'bill_actions.csv', folder / 'bill_status.csv', folder / 'listing.json'
    have, status = {}, {}
    if csv_path.exists() and not force:
        with csv_path.open(newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                have.setdefault(r['bill'], []).append((r['date'], r['chamber'], r['action']))
    if status_path.exists() and not force:
        with status_path.open(newline='', encoding='utf-8') as f:
            status = {r['bill']: r for r in csv.DictReader(f)}
    seen = json.loads(seen_path.read_text()) if seen_path.exists() and not force else {}
    print(f'{ga}th General Assembly: reading the file list at {REPO.format(ga=ga)}', flush=True)
    files = listing(ga, types)
    todo = [b for b, (_, stamp) in files.items() if force or seen.get(b) != stamp or b not in have or b not in status]
    print(f'  {len(files):,} bills listed; {len(todo):,} new or changed since the last run.', flush=True)

    def one(bill):
        url = files[bill][0]
        for attempt in range(3):
            try:
                body, _, _ = webget.get(url, UA, timeout=60)
                time.sleep(pause)
                return bill, status_of(decode(body))
            except Exception as e:  # noqa: BLE001 - retried, then reported
                err = e
                time.sleep(2 * (attempt + 1))
        return bill, err

    failed, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(one, b) for b in todo]):
            bill, res = fut.result()
            done += 1
            if isinstance(res, Exception):
                failed.append(f'{bill}: {res}')
            else:
                have[bill] = res.pop('actions')
                status[bill] = {'bill': bill, **res}
                seen[bill] = files[bill][1]
            if done % 250 == 0 or done == len(todo):
                print(f'  {done:,} of {len(todo):,} downloaded', flush=True)
                save(folder, have, status, seen)
    save(folder, have, status, seen)
    n = sum(len(v) for v in have.values())
    print(f'  Saved {n:,} actions for {len(have):,} bills to {csv_path}.', flush=True)
    if failed:
        print(f'  {len(failed):,} bills could not be downloaded; run this again to retry them:', *failed[:10], sep='\n    ')
    return not failed


def save(folder, have, status, seen):
    key = lambda b: (b.split()[0], int(b.split()[1]))

    def write(name, cols, rows):
        tmp = folder / (name + '.tmp')
        with tmp.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        tmp.replace(folder / name)
    write('bill_actions.csv', COLS, ([bill, i, d, c, a] for bill in sorted(have, key=key) for i, (d, c, a) in enumerate(have[bill], 1)))
    write('bill_status.csv', STATUS_COLS, ([status[b].get(c, '') for c in STATUS_COLS] for b in sorted(status, key=key)))
    (folder / 'listing.json').write_text(json.dumps(seen, indent=0, sort_keys=True))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ga', type=int, help='only this General Assembly')
    ap.add_argument('--force', action='store_true', help='download every bill again')
    a = ap.parse_args(argv)
    cfg = tomllib.loads((ROOT / 'config.toml').read_text())
    types = set(cfg['bills'].get('include_types') or ['HB', 'SB'])
    ok = True
    for s in cfg['bills']['sessions']:
        ga = int(s['ga'])
        if (a.ga and ga != a.ga) or not s.get('actions') or s.get('status') == 'upcoming':
            continue
        try:
            ok &= fetch_session(ga, DATA / s['actions'], types, a.force)
        except Exception as e:  # noqa: BLE001
            print(f'Could not update the {ga}th General Assembly: {e}')
            ok = False
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
