#!/usr/bin/env python3
"""Collect the witness slips filed on each bill: who supported or opposed it, and for whom, at which committee hearing.

    python pipeline/fetch_slips.py                 every session in config.toml with a [bills.sessions] slips setting
    python pipeline/fetch_slips.py --minutes 45    stop after 45 minutes (the next run picks up where this one stopped)
    python pipeline/fetch_slips.py --bills HB3769 SB2381     only these bills, checked again now

Anyone can file a witness slip before a committee hearing, for themselves or for an organization. ILGA publishes a text
export of every slip on each version of a bill (the bill as introduced and each amendment):
    Legislation|Name|Firm|Representation|Position|Committee|ScheduledDateTime
Firm is who the filer works for (often a lobbying firm) and Representation whom they spoke for (its client); Position is
Proponent, Opponent or No Position On Merits.

There is no bulk file, so each bill takes 3 to 6 requests: its Full Text tab once (for ILGA's internal bill id), its
witness slip page (for the list of versions) and one export per version. ILGA's robots.txt asks for 10 seconds between
requests, which this keeps, so a first pass over a session takes days of run time; each run does what fits in --minutes,
bills that had a committee vote or hearing first, then bills only assigned to a committee. After that a bill is checked
again only when ILGA lists new actions for it (pipeline/fetch_actions.py's bill_actions.csv).

Writes, per session, to Website/inputs/witness_slips/<ga>/ (saved to Dropbox by the update; not shown on the site):
    slips.csv     ga, bill, version_id, version, name, firm, representing, position, committee, hearing: one row per slip
    state.json    per bill: ILGA's id, the versions, how many actions it had when checked, when, and its slip count
"""
import argparse, csv, json, os, re, sys, time, tomllib, urllib.error
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import webget

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
CFG = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
UA = 'Mozilla/5.0 (Illinois Policy Agendas Project (IPAP) witness slip collector; research website)'
SITE = 'https://www.ilga.gov/Legislation/BillStatus'
PAUSE = 10                 # ILGA's robots.txt: Crawl-delay: 10
COLS = ['ga', 'bill', 'version_id', 'version', 'name', 'firm', 'representing', 'position', 'committee', 'hearing']
HEARD = re.compile(r'Do Pass|Recommends|Recommend Do Adopt|Postponed|Held in|Tabled', re.I)   # a committee took it up
ASSIGNED = re.compile(r'Assigned to|Re-assigned to', re.I)
pl = lambda n, w: f'{n:,} {w}{"" if n == 1 else "s"}'
age_days = lambda t: (datetime.now(timezone.utc) - datetime.strptime(t, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)).total_seconds() / 86400
NOW = lambda: datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Stop(Exception):
    """ILGA stopped answering; leave the rest for the next run."""


class Client:
    def __init__(self, deadline):
        self.deadline, self.fails, self.n, self.last = deadline, 0, 0, 0.0

    def get(self, url):
        if time.time() > self.deadline:
            raise TimeoutError
        wait = self.last + PAUSE - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        self.n += 1
        try:
            body, ctype, _ = webget.get(url, UA, timeout=60)
            self.fails = 0
            return body.decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):   # ILGA refuses automated requests: stop, and don't try to get around it
                raise Stop(f'ILGA refused the request (HTTP {e.code}) at {url}') from None
            self.fails += 1
            if self.fails >= 6:
                raise Stop(f'ILGA failed {self.fails} requests in a row (last: {url})')
            raise
        except Exception:
            self.fails += 1
            if self.fails >= 6:
                raise Stop(f'ILGA failed {self.fails} requests in a row (last: {url})')
            raise


def bill_parts(bill):
    t, n = re.match(r'([A-Z]+)\s*0*(\d+)$', bill.replace(' ', '')).groups()
    return t, n


def actions_of(folder):
    """{bill: [action, ...]} from fetch_actions.py's bill_actions.csv."""
    f = DATA / folder / 'bill_actions.csv'
    out = {}
    if f.exists():
        with f.open(encoding='utf-8', newline='') as fh:
            for r in csv.DictReader(fh):
                out.setdefault(r['bill'], []).append(r['action'])
    return out


def legid(c, ids, bill):
    t, n = bill_parts(bill)
    h = c.get(f'{SITE}/FullText?GAID={ids["gaid"]}&DocNum={n}&DocTypeID={t}&LegId=0&SessionID={ids["session"]}')
    found = [x for x in re.findall(r'LegI[Dd]=(\d+)', h) if x != '0']
    return found[0] if found else ''


def slips_of(c, ids, bill, lid):
    """[(version_id, version, rows)] for every version of the bill listed on its witness slip page."""
    t, n = bill_parts(bill)
    h = c.get(f'{SITE}/WitnessSlips?GAID={ids["gaid"]}&DocNum={n}&DocTypeID={t}&SessionID={ids["session"]}&LegId={lid}')
    versions = []
    for d, label in re.findall(r'href="[^"]*WitnessSlips\?LegDocId=(\d+)[^"]*"[^>]*>\s*([^<]+?)\s*<', h, re.I):
        if d not in [v[0] for v in versions]:
            versions.append((d, re.sub(r'\s+', ' ', label)))
    if not versions:   # a bill with one version may list none; its export link still names it
        versions = [(d, f'{t}{n}') for d in dict.fromkeys(re.findall(r'ExportWitnessSlips\?legdocid=(\d+)', h, re.I))]
    out = []
    for d, label in versions:
        text = c.get(f'{SITE}/ExportWitnessSlips?legdocid={d}&legislationname={t}{n}')
        rows = []
        for line in text.splitlines()[1:]:
            if not line.strip():
                continue
            p = line.split('|')
            if len(p) > 7:   # a '|' inside a name or firm: keep the last four fields in place
                p = p[:1] + ['|'.join(p[1:len(p) - 5])] + p[len(p) - 5:]
            p += [''] * (7 - len(p))
            rows.append([x.strip() for x in p[1:7]])
        out.append((d, label, rows))
    return out


def load(path):
    rows = {}
    if path.exists():
        with path.open(encoding='utf-8', newline='') as fh:
            for r in csv.DictReader(fh):
                rows.setdefault(r['bill'], []).append(r)
    return rows


def save(folder, rows, state):
    folder.mkdir(parents=True, exist_ok=True)
    order = lambda b: (bill_parts(b)[0], int(bill_parts(b)[1]))
    tmp = folder / 'slips.csv.tmp'
    with tmp.open('w', encoding='utf-8', newline='') as fh:
        w = csv.DictWriter(fh, COLS)
        w.writeheader()
        for b in sorted(rows, key=order):
            w.writerows(rows[b])
    tmp.replace(folder / 'slips.csv')
    tmp = folder / 'state.json.tmp'
    tmp.write_text(json.dumps(state, indent=0, sort_keys=True), encoding='utf-8')
    tmp.replace(folder / 'state.json')


def run_session(s, deadline, only=None):
    ga, ids = s['ga'], s['slips']
    folder = DATA / 'inputs' / 'witness_slips' / str(ga)
    state = json.loads((folder / 'state.json').read_text(encoding='utf-8')) if (folder / 'state.json').exists() else {}
    bills = state.setdefault('bills', {})
    rows = load(folder / 'slips.csv')
    if state.get('refused') and not only and age_days(state['refused']) < 7:
        print(f'{ga}th GA: ILGA refused the collector on {state["refused"][:10]}; trying again a week after that.')
        return True
    acts = actions_of(s['actions'])
    if not acts and not only:
        print(f'{ga}th GA: no bill_actions.csv yet in {s["actions"]} (run pipeline/fetch_actions.py first); skipped.')
        return True
    heard = [b for b, a in acts.items() if any(HEARD.search(x) for x in a)]
    assigned = [b for b, a in acts.items() if b not in set(heard) and any(ASSIGNED.search(x) for x in a)]
    due = lambda b: b not in bills or bills[b].get('n') != len(acts.get(b, []))
    queue = list(only) if only else [b for b in heard if due(b)] + [b for b in assigned if due(b)]
    print(f'{ga}th GA: {pl(len(heard), "bill")} had a committee vote or hearing, {len(assigned):,} more were only assigned to a '
          f'committee; {len(queue):,} to check (new, or with new actions since their last check).', flush=True)
    c = Client(deadline)
    done = new = 0
    stopped = ''
    try:
        for b in queue:
            e = bills.get(b, {})
            try:
                if not e.get('legid'):
                    e['legid'] = legid(c, ids, b)
                if not e['legid']:
                    e.update(n=len(acts.get(b, [])), t=NOW(), slips=0, note='no LegID on its Full Text tab')
                    bills[b] = e
                    continue
                got = slips_of(c, ids, b, e['legid'])
            except (TimeoutError, Stop):
                raise
            except Exception as ex:  # noqa: BLE001 - one bill's trouble: try it again next run
                print(f'  {b}: {type(ex).__name__}: {str(ex)[:160]}', flush=True)
                continue
            before = len(rows.get(b, []))
            rows[b] = [dict(zip(COLS, [str(ga), b, d, label, *r])) for d, label, rs in got for r in rs]
            if not rows[b]:
                del rows[b]
            e.update(n=len(acts.get(b, [])), t=NOW(), versions={d: label for d, label, _ in got},
                     slips=sum(len(rs) for *_, rs in got))
            e.pop('note', None)
            bills[b] = e
            done += 1
            new += max(0, e['slips'] - before)
            if done % 25 == 0:
                save(folder, rows, state)
    except TimeoutError:
        stopped = 'time'
    except Stop as ex:
        stopped = str(ex)
        if 'refused' in stopped:
            state['refused'] = NOW()
    if not stopped:
        state['pass'] = NOW()
    if 'refused' not in stopped:
        state.pop('refused', None)
    save(folder, rows, state)
    left = [b for b in heard + assigned if due(b)]
    all_rows = [r for rs in rows.values() for r in rs]
    pos = Counter(r['position'] for r in all_rows)
    orgs = Counter((r['representing'] or r['firm']).strip() for r in all_rows
                   if not re.fullmatch(r'\s*(self|na|n/a|none|individual|myself|citizen|)\s*', (r['representing'] or r['firm']), re.I))
    print(f'Witness slips, {ga}th GA: checked {pl(done, "bill")} this run ({pl(c.n, "request")}); {pl(new, "new slip")}.')
    print(f'  In the file: {pl(len(all_rows), "slip")} on {pl(len(rows), "bill")} ({", ".join(f"{k} {v:,}" for k, v in pos.most_common())}); '
          f'{len(orgs):,} organizations named.')
    print(f'  Still to check: {pl(len(left), "bill")} ({sum(1 for b in left if b in set(heard)):,} with a committee vote or hearing).')
    if orgs:
        print('  Most active organizations: ' + '; '.join(f'{o} ({n:,})' for o, n in orgs.most_common(8)))
    if stopped:
        print(f'  Stopped: {"time limit reached; the next run continues" if stopped == "time" else stopped}.')
    return stopped in ('', 'time') or 'refused' in stopped   # a refusal is reported, not a failed run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--minutes', type=float, default=45)
    ap.add_argument('--bills', nargs='*', help='only these bills, e.g. HB3769 SB2381 (first session with slips set up)')
    a = ap.parse_args(argv)
    deadline = time.time() + a.minutes * 60
    only = [f'{t} {n}' for t, n in (bill_parts(b.upper()) for b in a.bills)] if a.bills else None
    sessions = [s for s in CFG['bills']['sessions'] if s.get('slips') and s.get('status') != 'upcoming']
    if not sessions:
        print('No session has a slips setting in config.toml; nothing to do.')
        return 0
    ok = True
    for s in sessions[:1] if only else sessions:
        ok &= run_session(s, deadline, only)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
