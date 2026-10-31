#!/usr/bin/env python3
"""Find think tanks' new reports and add them to a spreadsheet for coders.

    python pipeline/fetch_reports.py            check every organization listed under [[thinktanks.sources]] in config.toml
    python pipeline/fetch_reports.py --days 60  look back further than usual

For each organization it reads the listing named in config.toml (a WordPress site's posts, or the links on a page),
skips reports already in the reports file or in the new-reports file, and visits each new one for its title, date,
summary and PDF. New reports are added to `[thinktanks] new` (inputs/thinktanks/new_reports.xlsx in Dropbox) with an
empty subtopic. Coders fill in `subtopic` and delete rows that aren't reports; a row goes on the site once it has a code.
Reports already offered once are remembered in found.json next to that file, so deleted rows don't come back.

It only looks at reports dated after the organization's latest report on file, less a few weeks (--days), so it
won't offer old items the team chose not to collect.
"""
import argparse, datetime as dt, html, json, os, re, sys, time, tomllib
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import pandas as pd

import webget  # downloads, with fixes for missing certificates

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
UA = 'Mozilla/5.0 (Illinois Policy Agendas Project (IPAP) report collector; research website)'
COLS = ['id', 'think_tank', 'title', 'date', 'year', 'abstract', 'url', 'pdf_url', 'subtopic', 'coder', 'notes', 'found_on']
PAUSE = 0.4


def get(url):
    body, ctype, final = webget.get(url, UA, timeout=40)
    time.sleep(PAUSE)
    return body, ctype, final


def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def key(url):
    """What identifies a report across moves of the same site: its last path segment, without an extension.
    (IGPA moved /reports/policy-spotlights/<name> to /the-repository/<name>.)"""
    seg = [x for x in unquote(urlparse(str(url)).path).split('/') if x]
    return re.sub(r'\.(pdf|html?|aspx?)$', '', seg[-1].lower()) if seg else str(url).lower()


def page_meta(raw, url):
    """Title, date (YYYY-MM-DD), summary and first PDF link of a report page."""
    h = raw.decode('utf-8', 'replace')

    def meta(*names):
        for n in names:
            m = re.search(rf'<meta[^>]+(?:property|name|itemprop)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']*)', h, re.I) or \
                re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name|itemprop)=["\']{re.escape(n)}["\']', h, re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
        return ''
    title = meta('og:title', 'twitter:title') or text((re.search(r'<title[^>]*>(.*?)</title>', h, re.S | re.I) or [None, ''])[1])
    title = re.sub(r'\s*[|–-]\s*[^|–-]{2,60}$', '', title) if re.search(r'\s[|–-]\s', title) else title   # drop " | Site name"
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', h, re.S | re.I)
    body = h[h1.end():] if h1 else h
    if h1 and len(title) < 12:   # some sites give only their own name as the page title
        title = text(h1.group(1)) or title
    date = meta('article:published_time', 'datePublished', 'date', 'dc.date', 'DC.date.issued')
    if not date:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', h) or re.search(r'<time[^>]+datetime=["\']([^"\']+)', h, re.I)
        date = m.group(1) if m else ''
    if not date:
        m = re.search(r'/(20\d\d)/(\d\d)/(\d\d)/', url)
        date = '-'.join(m.groups()) if m else ''
    if not date:   # a date standing alone in an element after the heading, e.g. <span>April 22, 2026</span>
        m = re.search(r'>\s*((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, 20\d\d)\s*<', body)
        date = m.group(1) if m else ''
    abstract = meta('og:description', 'description', 'twitter:description')
    words = {w for w in re.findall(r'[a-z]{5,}', title.lower())}
    if words and not words & set(re.findall(r'[a-z]{5,}', abstract.lower())):   # a site-wide blurb, not about this report
        abstract = ''
    if not abstract:
        abstract = next((t for t in (text(x) for x in re.findall(r'<p[^>]*>(.*?)</p>', body, re.S | re.I)) if len(t) > 80), '')[:600]
    # the report's own PDF: on the organization's site (files.example.org counts), not a source it cites
    site = '.'.join(urlparse(url).hostname.split('.')[-2:])
    pdf = next((u for u in (urljoin(url, p) for p in re.findall(r'href=["\']([^"\']+\.pdf)(?:[?#][^"\']*)?["\']', h, re.I))
                if (urlparse(u).hostname or '').endswith(site)), '')
    return title, iso(date), abstract, pdf


def iso(s):
    s = str(s or '').strip()
    for f in (lambda x: dt.date.fromisoformat(x[:10]), lambda x: dt.datetime.strptime(x, '%B %d, %Y').date(), lambda x: dt.datetime.strptime(x, '%b %d, %Y').date()):
        try:
            return f(s).isoformat()
        except ValueError:
            pass
    return ''


def candidates(src, cutoff):
    """[(url, title, date, abstract)] from one organization's listing; title etc. may be empty (filled from the page)."""
    if src['kind'] == 'wp':
        out, page = [], 1
        while page <= 10:
            api = f"{src['site'].rstrip('/')}/wp-json/wp/v2/{src.get('type', 'posts')}?after={cutoff}T00:00:00&per_page=50&page={page}&_fields=date,link,title,excerpt"
            body, _, _ = get(api)
            items = json.loads(body)
            if not isinstance(items, list) or not items:
                break
            out += [(i['link'], text(i['title']['rendered']), iso(i['date']), text(i.get('excerpt', {}).get('rendered', ''))) for i in items]
            if len(items) < 50:
                break
            page += 1
        return out
    if src['kind'] == 'links':
        body, _, final = get(src['url'])
        pat = re.compile(src['match'], re.I)
        seen, out = set(), []
        for href in re.findall(r'href=["\']([^"\'#]+)', body.decode('utf-8', 'replace'), re.I):
            u = urljoin(final, html.unescape(href)).split('?')[0]
            if pat.search(urlparse(u).path) and u not in seen:
                seen.add(u)
                out.append((u, '', '', ''))
        return out
    raise ValueError(f'unknown kind {src["kind"]!r}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=45, help='also look this many days before the latest report on file (default 45)')
    a = ap.parse_args(argv)
    cfg = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
    tt = cfg['thinktanks']
    c = tt['columns']
    main_df = pd.read_excel(DATA / tt['file']) if (DATA / tt['file']).exists() else pd.DataFrame(columns=list(c.values()))
    new_path = DATA / tt.get('new', 'inputs/thinktanks/new_reports.xlsx')
    try:
        new_df = pd.read_excel(new_path) if new_path.exists() else pd.DataFrame(columns=COLS)
    except PermissionError:
        print(f'{new_path.name} is open in Excel. Close it and try again.')
        return 1
    found_path = new_path.with_name('found.json')
    found = json.loads(found_path.read_text()) if found_path.exists() else {}

    known = {}
    for df, cols in ((main_df, c), (new_df, {'org': 'think_tank', 'url': 'url', 'pdf': 'pdf_url', 'date': 'date'})):
        for r in df.to_dict('records'):
            org = str(r.get(cols['org']) or '')
            k = known.setdefault(org, {'keys': set(), 'last': '', 'pdfs': set()})
            for u in (r.get(cols['url']), r.get(cols['pdf'])):
                if isinstance(u, str) and u.strip():
                    k['keys'].add(key(u))
            if isinstance(r.get(cols['pdf']), str) and r[cols['pdf']].strip():
                k['pdfs'].add(r[cols['pdf']].strip())
            d = iso(r.get(cols['date']))
            k['last'] = max(k['last'], d)
    next_id = int(max([0, *pd.to_numeric(main_df[c['id']], errors='coerce').dropna(), *pd.to_numeric(new_df['id'], errors='coerce').dropna()])) + 1

    added, problems, today = [], [], dt.date.today().isoformat()
    for src in tt.get('sources', []):
        org = src['org']
        k = known.get(org, {'keys': set(), 'last': '', 'pdfs': set()})
        last = k['last'] or (dt.date.today() - dt.timedelta(days=365)).isoformat()
        cutoff = (dt.date.fromisoformat(last) - dt.timedelta(days=a.days)).isoformat()
        offered = set(found.get(org, []))
        print(f'{org}: looking for reports after {cutoff} …', flush=True)
        try:
            cands = candidates(src, cutoff)
        except Exception as e:  # noqa: BLE001 - reported, then the next organization
            problems.append(f'{org}: could not read the listing ({e})')
            print(f'  could not read the listing: {e}', flush=True)
            continue
        n = 0
        for url, title, date, abstract in cands:
            kk = key(url)
            if kk in k['keys'] or kk in offered:
                continue
            pdf = url if url.lower().endswith('.pdf') else ''
            if pdf:
                m = re.search(r'/uploads/(\d{4})/(\d\d)/', url)
                date = date or (f'{m.group(1)}-{m.group(2)}-01' if m else '')
                title = title or re.sub(r'[-_]+', ' ', kk).strip().title()
            else:
                try:
                    body, ctype, final = get(url)
                    t2, d2, a2, pdf = page_meta(body, final) if 'html' in ctype else ('', '', '', '')
                    title, date, abstract = title or t2, date or d2, abstract or a2
                    if pdf in k['pdfs']:   # already another report's: an organization-wide PDF linked from every page
                        pdf = ''
                except Exception as e:  # noqa: BLE001
                    problems.append(f'{org}: {url} ({e})')
                    continue
            if date and date < cutoff:
                continue
            offered.add(kk)
            added.append({'id': next_id, 'think_tank': org, 'title': title, 'date': date or None, 'year': int(date[:4]) if date else None,
                          'abstract': abstract, 'url': url, 'pdf_url': pdf or None, 'subtopic': None, 'coder': None, 'notes': None, 'found_on': today})
            next_id += 1
            n += 1
        found[org] = sorted(offered)
        print(f'  {n} new', flush=True)

    if added:
        out = pd.concat([new_df, pd.DataFrame(added)], ignore_index=True)[[x for x in COLS if x in set(new_df.columns) | set(COLS)]]
        out['date'] = pd.to_datetime(out['date'], errors='coerce').dt.date
        new_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.to_excel(new_path, index=False)
        except PermissionError:
            print(f'{new_path.name} is open in Excel. Close it and try again.')
            return 1
    found_path.parent.mkdir(parents=True, exist_ok=True)
    found_path.write_text(json.dumps(found, indent=1, sort_keys=True))
    print(f'\nAdded {len(added)} new report{"s" if len(added) != 1 else ""} to {new_path}.' if added else '\nNo new reports.')
    if added:
        print('Next: fill in `subtopic` for each (delete rows that are not reports), then Check data and Publish.')
    hand = sorted({str(o) for o in main_df[c['org']].dropna()} - {s['org'] for s in tt.get('sources', [])})
    if hand:
        print('Collected by hand (no source in config.toml): ' + ', '.join(hand))
    if problems:
        print(f'\n{len(problems)} problem{"s" if len(problems) > 1 else ""}:', *problems[:20], sep='\n  ')
    # some sites refusing is normal (they are collected by hand); fail only when no listing could be read at all
    listed = len(tt.get('sources', [])) - sum(1 for p in problems if 'could not read the listing' in p)
    return 1 if problems and listed <= 0 else 0


if __name__ == '__main__':
    sys.exit(main())
