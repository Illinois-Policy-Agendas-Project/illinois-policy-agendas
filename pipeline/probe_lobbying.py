#!/usr/bin/env python3
"""One-off check for lobbying data linked to bills: what ILGA's witness slips and the Secretary of State's lobbyist
files contain. Writes a report and sample pages to probe/ (attached to the GitHub run); changes nothing else.

    python pipeline/probe_lobbying.py HB3769 SB774 ...     bills of the 104th General Assembly

1. robots.txt of the ILGA hosts (whether automated reading of slip pages is allowed).
2. For each bill: its ILGA bill status page, every link on it about witness slips or hearings, and the pages those
   links lead to (one more level), with their table headers, row counts and a few rows.
3. The Illinois Data Portal's "Daily Lobbyist Data": each file's columns, size and first rows.
"""
import html, json, re, sys, time, urllib.parse
from pathlib import Path

import webget

UA = 'Mozilla/5.0 (Illinois Policy Agendas Project (IPAP) research check; https://www.illinoisagendas.org)'
OUT = Path('probe')
GA, GAID, SESSION = 104, 18, 114
PAUSE = 1.5
LOG = []


def log(s=''):
    print(s, flush=True)
    LOG.append(s)


def fetch(url, limit=None):
    time.sleep(PAUSE)
    try:
        body, ctype, final = webget.get(url, UA, timeout=40, limit=limit)
        return body, ctype, final, None
    except Exception as e:  # noqa: BLE001
        return b'', '', url, f'{type(e).__name__}: {e}'


def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def links(body, base):
    h = body.decode('utf-8', 'replace')
    out = []
    for m in re.finditer(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', h, re.I | re.S):
        u = urllib.parse.urljoin(base, html.unescape(m.group(1)))
        out.append((u, text(m.group(2))[:80]))
    return out


def tables(body):
    h = body.decode('utf-8', 'replace')
    res = []
    for t in re.findall(r'<table\b.*?</table>', h, re.I | re.S):
        rows = re.findall(r'<tr\b.*?</tr>', t, re.I | re.S)
        head = [text(c) for c in re.findall(r'<th\b.*?</th>', t, re.I | re.S)]
        cells = [[text(c) for c in re.findall(r'<t[dh]\b.*?</t[dh]>', r, re.I | re.S)] for r in rows]
        res.append({'head': head, 'rows': len(rows), 'sample': [c for c in cells if c and c != head][:4]})
    return res


def describe(name, url, body, ctype, final, err, save=None):
    log(f'  {name}: {url}')
    if err:
        log(f'    ERROR {err}')
        return
    log(f'    -> {final} | {ctype} | {len(body):,} bytes')
    if save:
        (OUT / 'samples').mkdir(parents=True, exist_ok=True)
        (OUT / 'samples' / save).write_bytes(body)
        log(f'    saved samples/{save}')
    if 'json' in ctype:
        log('    JSON: ' + body[:600].decode('utf-8', 'replace'))
    for i, t in enumerate(tables(body)[:6]):
        log(f'    table {i}: {t["rows"]} rows; header {t["head"][:12]}')
        for r in t['sample'][:3]:
            log(f'      row: {r[:10]}')
    # pages built by script: show the script and data endpoints they call
    h = body.decode('utf-8', 'replace')
    for m in sorted(set(re.findall(r'["\'](/[A-Za-z0-9_/\-]*(?:api|Api|API|Slip|slip|Witness|witness|Hearing|hearing)[A-Za-z0-9_/\-\.?=&]*)["\']', h)))[:15]:
        log(f'    script/endpoint reference: {m}')


def ilga_part(bills):
    log('## 1. robots.txt')
    for host in ('www.ilga.gov', 'ilga.gov', 'my.ilga.gov', 'witnessslips.ilga.gov'):
        body, ctype, final, err = fetch(f'https://{host}/robots.txt')
        log(f'- {host}: ' + (err or f'{final}\n```\n{body.decode("utf-8", "replace")[:1500]}\n```'))
    log('\n## 2. Witness slips, bill by bill')
    seen_patterns, saved = {}, 0
    for b in bills:
        t, n = re.match(r'([A-Z]+)(\d+)', b).groups()
        log(f'\n### {t} {n}')
        url = f'https://www.ilga.gov/Legislation/BillStatus?GAID={GAID}&DocNum={n}&DocTypeID={t}&SessionID={SESSION}&GA={GA}'
        body, ctype, final, err = fetch(url)
        describe('bill status', url, body, ctype, final, err, save=f'{b}_status.html' if saved < 3 else None)
        if err:
            continue
        cand = [(u, label) for u, label in links(body, final) if re.search(r'witness|slip|hearing', u + ' ' + label, re.I)]
        uniq = list(dict.fromkeys(cand))
        log(f'  witness/hearing links on the page: {len(uniq)}')
        for u, label in uniq[:12]:
            log(f'    - [{label}] {u}')
        for i, (u, label) in enumerate(uniq[:3]):
            if not re.search(r'(^|\.)ilga\.gov$', urllib.parse.urlparse(u).hostname or ''):
                continue
            pat = re.sub(r'\d+', 'N', urllib.parse.urlparse(u).path)
            seen_patterns[pat] = seen_patterns.get(pat, 0) + 1
            body2, ctype2, final2, err2 = fetch(u)
            keep = saved < 6 and seen_patterns[pat] <= 2
            describe(f'level 1 [{label}]', u, body2, ctype2, final2, err2, save=f'{b}_l1_{i}.html' if keep else None)
            saved += keep
            if err2:
                continue
            deeper = [(v, l) for v, l in links(body2, final2) if re.search(r'witness|slip', v + ' ' + l, re.I) and v != u]
            deeper = list(dict.fromkeys(deeper))
            log(f'    deeper witness links: {len(deeper)}')
            for v, l in deeper[:6]:
                log(f'      - [{l}] {v}')
            for j, (v, l) in enumerate(deeper[:2]):
                body3, ctype3, final3, err3 = fetch(v)
                keep = saved < 8
                describe(f'level 2 [{l}]', v, body3, ctype3, final3, err3, save=f'{b}_l2_{i}_{j}.html' if keep else None)
                saved += keep
    log('\nLink path patterns followed: ' + json.dumps(seen_patterns))


def portal_part():
    log('\n## 3. Illinois Data Portal: Daily Lobbyist Data')
    api = 'https://data.illinois.gov/api/3/action/package_show?id=daily-lobbyist-data'
    body, ctype, final, err = fetch(api)
    if err:
        log(f'- package_show: {err}')
        body, ctype, final, err = fetch('https://data.illinois.gov/dataset/daily-lobbyist-data')
        describe('dataset page', 'https://data.illinois.gov/dataset/daily-lobbyist-data', body, ctype, final, err, save='portal_page.html')
        res = [{'name': l, 'url': u} for u, l in links(body, final) if re.search(r'\.csv|download', u, re.I)]
    else:
        try:
            d = json.loads(body)['result']
            log(f'- {d.get("title")} | updated {d.get("metadata_modified")}\n  {text(d.get("notes") or "")[:600]}')
            res = d.get('resources', [])
        except Exception as e:  # noqa: BLE001
            log(f'- could not read package_show: {e}: {body[:300]!r}')
            res = []
    for r in res[:25]:
        u = r.get('url')
        log(f'\n- {r.get("name")} | {r.get("format", "")} | {u} | modified {r.get("last_modified") or r.get("metadata_modified")}')
        if r.get('description'):
            log(f'  {text(r["description"])[:300]}')
        if not u:
            continue
        b, ct, fin, e = fetch(u, limit=200_000)
        if e:
            log(f'  ERROR {e}')
            continue
        lines = b.decode('utf-8', 'replace').splitlines()
        log(f'  {ct} | first {len(b):,} bytes read')
        for line in lines[:4]:
            log(f'  | {line[:400]}')
        hits = [line for line in lines if re.search(r'\b(HB|SB|HJR|SJR|HR|SR)\s*0*\d{1,4}\b', line)]
        log(f'  lines in the sample mentioning a bill number: {len(hits)} of {max(0, len(lines) - 1)}')
        for line in hits[:3]:
            log(f'  bill? {line[:300]}')


def main():
    bills = [b.strip().upper() for b in (sys.argv[1:] or []) if b.strip()]
    OUT.mkdir(exist_ok=True)
    log(f'# Lobbying data check ({time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())})\n')
    ilga_part(bills)
    portal_part()
    (OUT / 'report.md').write_text('\n'.join(LOG) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
