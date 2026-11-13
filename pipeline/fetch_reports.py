#!/usr/bin/env python3
"""Find think tanks' new reports and add them to a spreadsheet for coders.

    python pipeline/fetch_reports.py            check every organization listed under [[thinktanks.sources]] in config.toml
    python pipeline/fetch_reports.py --days 60  look back further than usual
    python pipeline/fetch_reports.py --only "Civic Federation" --dry-run   count what one source would add, change nothing

For each organization it reads the listing named in config.toml (a WordPress site's posts, or the links on a page),
skips reports already in the reports file or in the new-reports file, and visits each new one for its title, date,
summary and PDF. New reports are added to `[thinktanks] new` (inputs/thinktanks/new_reports.xlsx in Dropbox) with an
empty subtopic. Coders fill in `subtopic` and delete rows that aren't reports; a row goes on the site once it has a code.
Reports already offered once are remembered in found.json next to that file, so deleted rows don't come back.

It only looks at reports dated after the organization's latest report on file, less a few weeks (--days), so it
won't offer old items the team chose not to collect. An organization with nothing on file yet is collected from
`[thinktanks] first_since` (default 2006-01-01, the start of the data), so a newly added source brings its back catalogue.

It reads only what each site's robots.txt allows the collector, and skips a site that refuses it.
"""
import argparse, datetime as dt, html, json, os, re, sys, time, tomllib, urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote, quote, urlsplit, urlunsplit

import pandas as pd

import webget  # downloads, with fixes for missing certificates

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
UA = 'Mozilla/5.0 (Illinois Policy Agendas Project (IPAP) report collector; research website)'
COLS = ['id', 'think_tank', 'title', 'date', 'year', 'abstract', 'url', 'pdf_url', 'subtopic', 'coder', 'notes', 'found_on']
PAUSE = 0.4


ROBOTS, DELAY, LAST = {}, {}, {}   # robots.txt per site; seconds to wait between requests to a site; when we last asked it


def allowed(url):
    """Whether the site's robots.txt lets the collector read this address. A robots.txt we may not read (401/403)
    counts as a refusal; a missing one (404 or similar) as permission, as crawlers do."""
    u = urlparse(url); root = f'{u.scheme}://{u.netloc}'
    if root not in ROBOTS:
        rp = urllib.robotparser.RobotFileParser()
        try:
            body, _, _ = webget.get(root + '/robots.txt', UA, timeout=20)
            rp.parse(body.decode('utf-8', 'replace').splitlines())
        except Exception as e:  # noqa: BLE001
            code = getattr(e, 'code', None)
            rp.parse(['User-agent: *', 'Disallow: /'] if code in (401, 403) else [])
        ROBOTS[root] = rp
        DELAY[u.netloc] = max(PAUSE, float(rp.crawl_delay(UA) or 0))   # a site's Crawl-delay is honoured
    return ROBOTS[root].can_fetch(UA, url)


def requote(url):
    """The address with any character a request can't carry percent-encoded (existing %xx escapes kept)."""
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.netloc, quote(u.path, safe="/%:@!$&'()*+,;=-._~"), quote(u.query, safe="=&%+/:,;@!$'()*-._~"), u.fragment))


def get(url):
    url = requote(url)
    if not allowed(url):
        raise PermissionError(f'robots.txt does not allow {url}')
    host = urlparse(url).netloc
    for attempt in (1, 2, 3):
        wait = DELAY.get(host, PAUSE) - (time.time() - LAST.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        LAST[host] = time.time()
        try:
            body, ctype, final = webget.get(url, UA, timeout=40)
            break
        except Exception as e:  # noqa: BLE001
            code = getattr(e, 'code', None)
            if code == 429:   # too many requests: wait as long as the site asks (within reason) and go slower from now on
                DELAY[host] = min(30.0, max(DELAY.get(host, PAUSE) * 3, 3.0))
                try:
                    retry = float((getattr(e, 'headers', None) or {}).get('Retry-After') or 0)
                except (TypeError, ValueError):
                    retry = 0
                if attempt < 3:
                    time.sleep(min(120.0, max(retry, 20.0)))
                    continue
            if attempt == 3 or code in (401, 403, 404, 410):
                raise
            time.sleep(3)   # a dropped connection or a cut-off download gets another try
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
        out, page, extra = [], 1, ('&' + src['query'].lstrip('&?')) if src.get('query') else ''
        while page <= 40:
            api = f"{src['site'].rstrip('/')}/wp-json/wp/v2/{src.get('type', 'posts')}?after={cutoff}T00:00:00&per_page=50&page={page}&_fields=date,link,title,excerpt{extra}"
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
        pat, skip = re.compile(src['match'], re.I), re.compile(src['exclude'], re.I) if src.get('exclude') else None
        seen, out = set(), []
        for page_url in listing_urls(src):
            try:
                body, _, final = get(page_url)
            except Exception:  # noqa: BLE001 - a later page of a long listing may not exist; the first must
                if page_url == listing_urls(src)[0]:
                    raise
                continue
            site = '.'.join((urlparse(final).hostname or '').split('.')[-2:])
            for href, inner in re.findall(r'<a\b[^>]*?href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', body.decode('utf-8', 'replace'), re.I | re.S):
                u = urljoin(final, html.unescape(href)).split('?')[0]
                if not src.get('any_host') and not (urlparse(u).hostname or '').endswith(site):
                    continue
                if pat.search(urlparse(u).path) and not (skip and skip.search(urlparse(u).path)) and u not in seen:
                    seen.add(u)
                    label = text(re.sub(r'(?is)<(style|script|svg)\b.*?</\1>', ' ', inner))
                    label = re.sub(r'(?i)[\s|·•-]*\b(view\s*/\s*download|download( pdf)?|view( pdf)?)\s*$', '', label).strip()
                    # the link's words make a better title than a file name, unless they are just "View report" or "Read more"
                    if len(label) < 8 or re.fullmatch(r'(?i)(view|download|read|open|see|get|click|learn|full|more|here|pdf)\b.{0,20}', label):
                        label = ''
                    out.append((u, label, '', ''))
        return out
    if src['kind'] == 'rss':
        out = []
        for feed_url in listing_urls(src):
            try:
                body, _, _ = get(feed_url)
            except Exception:  # noqa: BLE001 - a later page of a paged feed may not exist; the first must
                if feed_url == listing_urls(src)[0]:
                    raise
                continue
            out += feed_items(src, body.decode('utf-8', 'replace'))
        return out
    raise ValueError(f'unknown kind {src["kind"]!r}')


def feed_items(src, x):
    """[(url, title, date, summary)] from an RSS or Atom feed."""
    out = []
    for item in re.findall(r'<(?:item|entry)\b.*?</(?:item|entry)>', x, re.S | re.I):
        def tag(*names):
            for n in names:
                m = re.search(rf'<{n}\b[^>]*>(.*?)</{n}>', item, re.S | re.I)
                if m:
                    return html.unescape(re.sub(r'^<!\[CDATA\[|\]\]>$', '', m.group(1).strip()))
            return ''
        link = tag('link') or (re.search(r'<link[^>]+href=["\']([^"\']+)', item) or [None, ''])[1]
        link = (re.search(r'href=["\']([^"\']+)', link) or [None, link])[1]   # some feeds wrap the link in markup
        date = tag('pubDate', 'published', 'updated', 'dc:date')
        try:
            date = dt.datetime.strptime(re.sub(r'\s+\S+$', '', date), '%a, %d %b %Y %H:%M:%S').date().isoformat() if ',' in date[:5] else iso(date)
        except ValueError:
            date = iso(date)
        if link:
            out.append((urljoin(src['url'], link), text(tag('title')), date, text(tag('description', 'summary'))[:600]))
    return out


def listing_urls(src):
    """The listing pages of a `links` or `rss` source: `url`, a list `urls`, or `pages = [first, last]` filled into {n} in `url`."""
    if src.get('urls'):
        return list(src['urls'])
    if src.get('pages'):
        a, b = src['pages']
        return [src['url'].format(n=n) for n in range(a, b + 1)]
    return [src['url']]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=45, help='also look this many days before the latest report on file (default 45)')
    ap.add_argument('--only', action='append', help='check only this organization (repeatable)')
    ap.add_argument('--dry-run', action='store_true', help='list what would be added; write nothing')
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
    found.setdefault('_retry', {})

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
    first_since = iso(tt.get('first_since', '2006-01-01')) or '2006-01-01'
    for src in tt.get('sources', []):
        org = src['org']
        if a.only and org not in a.only:
            continue
        k = known.get(org, {'keys': set(), 'last': '', 'pdfs': set()})
        cutoff = (dt.date.fromisoformat(k['last']) - dt.timedelta(days=a.days)).isoformat() if k['last'] else first_since
        if org in tt.get('recheck', []):   # look back to the start again; reports on file or deleted by coders are still skipped
            cutoff = first_since
        offered = set(found.get(org, []))
        print(f'{org}: looking for reports after {cutoff} …', flush=True)
        retry = {tuple(x) for x in found.get('_retry', {}).get(org, [])}
        try:
            cands = [tuple(x) for x in retry] + candidates(src, cutoff)
        except Exception as e:  # noqa: BLE001 - reported, then the next organization
            problems.append(f'{org}: could not read the listing ({e})')
            print(f'  could not read the listing: {e}', flush=True)
            continue
        n, again = 0, []
        for url, title, date, abstract in cands:
            was_retry = (url, title, date, abstract) in retry
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
                    refused = getattr(e, 'code', None) in (401, 402, 403) or isinstance(e, PermissionError)
                    if refused and src['kind'] == 'rss' and title:
                        pass   # the site won't show us the page, but its open feed gave the title, date and summary: use those
                    else:
                        problems.append(f'{org}: {url} ({e})')
                        if not refused and getattr(e, 'code', None) not in (404, 410):
                            again.append([url, title, date, abstract])   # a passing failure (too many requests, timeout): next run
                        continue
            if date and date < cutoff and not was_retry:
                continue
            offered.add(kk)
            added.append({'id': next_id, 'think_tank': org, 'title': title, 'date': date or None, 'year': int(date[:4]) if date else None,
                          'abstract': abstract, 'url': url, 'pdf_url': pdf or None, 'subtopic': None, 'coder': None, 'notes': None, 'found_on': today})
            next_id += 1
            n += 1
        found[org] = sorted(offered)
        found.setdefault('_retry', {})[org] = again
        if not again:
            found['_retry'].pop(org, None)
        print(f'  {n} new', flush=True)

    if a.dry_run:
        by = {}
        for r in added:
            by.setdefault(r['think_tank'], []).append(r)
        for org, rows in by.items():
            ds = sorted(r['date'] for r in rows if r['date'])
            print(f'\n{org}: {len(rows)} (dated {ds[0] if ds else "-"} to {ds[-1] if ds else "-"}; {sum(1 for r in rows if not r["date"])} undated)')
            for r in rows[:6]:
                print(f"   {r['date'] or '----------'}  {r['title'][:80]}  {r['url'][:90]}")
        print(f'\nDry run: {len(added)} would be added. Nothing was written.')
        if problems:
            print(f'{len(problems)} problem(s):', *problems[:40], sep='\n  ')
        return 0
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
