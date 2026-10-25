#!/usr/bin/env python3
"""Collect each legislator's party, district and official headshot from the General Assembly website.

    python pipeline/fetch_legislators.py              update the member list; download photos that are missing
    python pipeline/fetch_legislators.py --force      download every photo again
    python pipeline/fetch_legislators.py --chamber Senate

It reads the member lists at https://ilga.gov/House/Members and https://ilga.gov/Senate/Members, then each member's
own page for their photo (and for party and district when the list doesn't show them). It writes
content/legislators/members.csv (chamber, district, name, party, member page, photo file) and saves photos in
content/legislators/photos/. Then it writes build/legislators.html so someone can check every row and photo.

The site matches these names to the sponsors in the bill data (same chamber, same first and last name), so legislators
who left before the current list was posted keep no photo or party until a row for them is added by hand.
Rows edited by hand are kept: a row whose `source` column says "manual" is never overwritten.
Official headshots are public records of the General Assembly; credit them as "Photos: Illinois General Assembly".
"""
import argparse, csv, html, re, sys, time, tomllib, unicodedata, urllib.error, urllib.request
from html.parser import HTMLParser
from pathlib import Path

import webget  # downloads, with fixes for missing certificates
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
UA = 'Mozilla/5.0 (IllinoisPolicyAgendas member collector; research website)'
LISTS = {'House': 'https://ilga.gov/House/Members', 'Senate': 'https://ilga.gov/Senate/Members'}
COLS = ['chamber', 'district', 'name', 'party', 'url', 'photo', 'source']
PARTY = {'d': 'Democratic', 'r': 'Republican', 'i': 'Independent', 'democrat': 'Democratic', 'democratic': 'Democratic', 'republican': 'Republican', 'independent': 'Independent'}
MEMBER_LINK = re.compile(r'/(?:House|Senate)/Members/(?:Details|Detail|Profile)/\d+|[?&]MemberID=\d+', re.I)


def get(url, timeout=25):
    return webget.get(url, UA, timeout)


def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(s or '')).strip()


def first_last(name):
    """ILGA sometimes writes "Last, First M." -- turn it into "First M. Last"."""
    name = clean(name)
    m = re.match(r'^([^,]+),\s*(.+?)(,\s*(Jr\.?|Sr\.?|II|III|IV))?$', name)
    return f'{m.group(2)} {m.group(1)}{m.group(3) or ""}' if m and not re.search(r'\b(Jr|Sr|II|III|IV)\.?$', m.group(1)) else name


def party_of(text):
    t = clean(text).lower().strip('() ')
    if t in PARTY:
        return PARTY[t]
    m = re.search(r'\b(democrat(?:ic)?|republican|independent)\b', t)
    return PARTY[m.group(1)] if m else ''


class Rows(HTMLParser):
    """Table rows of a member list: for each <tr>, its cells' text and every link (href, text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.link = [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'tr':
            self.row = {'cells': [], 'links': []}
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
        elif tag == 'a' and self.row is not None:
            self.link = [a.get('href') or '', []]

    def handle_endtag(self, tag):
        if tag == 'a' and self.link is not None and self.row is not None:
            self.row['links'].append((self.link[0], clean(''.join(self.link[1]))))
            self.link = None
        elif tag in ('td', 'th') and self.cell is not None and self.row is not None:
            self.row['cells'].append(clean(''.join(self.cell)))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        if self.link is not None:
            self.link[1].append(data)


class Page(HTMLParser):
    """A member's own page: headings, images (src, alt, class) and the social-sharing image."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.imgs, self.og, self.heads, self.text, self._h = [], '', [], [], None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag == 'img':
            self.imgs.append((a.get('src') or a.get('data-src') or '', ' '.join([a.get('alt', ''), a.get('class', ''), a.get('id', ''), a.get('title', '')])))
        elif tag == 'meta' and (a.get('property') or a.get('name', '')).lower() == 'og:image':
            self.og = a.get('content', '')
        elif tag in ('h1', 'h2'):
            self._h = []

    def handle_endtag(self, tag):
        if tag in ('h1', 'h2') and self._h is not None:
            self.heads.append(clean(''.join(self._h)))
            self._h = None

    def handle_data(self, data):
        self.text.append(data)
        if self._h is not None:
            self._h.append(data)


def read_list(chamber, log):
    """Members from the chamber's list page: [{name, url, party, district}]."""
    page, _, final = get(LISTS[chamber])
    text = page.decode('utf-8', 'replace')
    p = Rows()
    p.feed(text)
    out, seen = [], set()
    for r in p.rows:
        link = next(((h, t) for h, t in r['links'] if MEMBER_LINK.search(h) and t), None)
        if not link:
            continue
        url = urljoin(final, link[0])
        if url in seen:
            continue
        seen.add(url)
        party = next((party_of(c) for c in r['cells'] if party_of(c)), '')
        district = next((c for c in r['cells'] if re.fullmatch(r'\d{1,3}', c)), '')
        out.append({'name': first_last(link[1]), 'url': url, 'party': party, 'district': district})
    if not out:  # not a table: take every member link on the page, and look for party/district near it
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
            href, inner = m.group(1), clean(re.sub(r'<[^>]+>', ' ', m.group(2)))
            if not MEMBER_LINK.search(href) or not inner:
                continue
            url = urljoin(final, href)
            if url in seen:
                continue
            seen.add(url)
            near = clean(re.sub(r'<[^>]+>', ' ', text[m.end():m.end() + 600]))
            pm = re.search(r'\b(Democrat(?:ic)?|Republican|Independent)\b|\(([DRI])\)|\b([DRI])\b', near)
            dm = re.search(r'District\s*(\d{1,3})', near, re.I)
            out.append({'name': first_last(inner), 'url': url, 'party': party_of(next(g for g in pm.groups() if g)) if pm else '', 'district': dm.group(1) if dm else ''})
    log(f'{chamber}: {len(out)} members on {LISTS[chamber]}')
    return out


def read_member(m, log):
    """Fill in photo (and party/district if missing) from the member's own page."""
    page, _, final = get(m['url'])
    p = Page()
    p.feed(page.decode('utf-8', 'replace'))
    body = clean(' '.join(p.text))
    if not m['party']:
        pm = re.search(r'\b(Democrat(?:ic)?|Republican|Independent)\b', body)
        m['party'] = party_of(pm.group(1)) if pm else ''
    if not m['district']:
        dm = re.search(r'(\d{1,3})(?:st|nd|rd|th)?\s+(?:Representative|Legislative)?\s*District|District\s*(\d{1,3})', body, re.I)
        m['district'] = next((g for g in dm.groups() if g), '') if dm else ''
    last = m['name'].replace('"', ' ').split()[-1].lower() if m['name'] else ''
    best = None
    for src, meta in p.imgs:
        if not src or src.startswith('data:'):
            continue
        s, t = src.lower(), meta.lower()
        score = (40 if re.search(r'member|headshot|portrait|photo', s) else 0) + (30 if last and last in (s + ' ' + t) else 0) + (10 if re.search(r'photo|headshot|member', t) else 0) \
            - (60 if re.search(r'logo|seal|icon|banner|sprite|flag', s + ' ' + t) else 0)
        if score > 0 and (best is None or score > best[0]):
            best = (score, urljoin(final, src))
    return best[1] if best else (urljoin(final, p.og) if p.og else None)


def save_photo(url, dest):
    data, ctype, _ = get(url)
    if len(data) < 2000 or not (ctype.startswith('image/') or data[:3] == b'\xff\xd8\xff' or data[:4] == b'\x89PNG'):
        raise ValueError(f'not a photo ({ctype or "unknown type"}, {len(data)} bytes)')
    try:  # keep the files small: 300 pixels wide is plenty for the site
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data)).convert('RGB')
        if im.width > 300:
            im = im.resize((300, round(im.height * 300 / im.width)))
        dest = dest.with_suffix('.jpg')
        im.save(dest, 'JPEG', quality=85, optimize=True)
    except ImportError:
        dest = dest.with_suffix('.png' if data[:4] == b'\x89PNG' else '.jpg')
        dest.write_bytes(data)
    return dest


def slugify(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def review_page(rows, path, photo_dir):
    cards = ''.join(f'''<div class="c"><div class="img">{f'<img src="../{html.escape(str((photo_dir / r["photo"]).relative_to(ROOT)))}" alt="">' if r.get("photo") else '<span>no photo</span>'}</div>
      <b>{html.escape(r["name"])}</b><div class="m">{html.escape(r["chamber"])} · District {html.escape(r["district"] or "?")} · {html.escape(r["party"] or "party unknown")}</div>
      <div class="m"><a href="{html.escape(r["url"])}">member page</a>{' · edited by hand' if r.get('source') == 'manual' else ''}</div></div>''' for r in rows)
    path.write_text(f'''<!doctype html><meta charset="utf-8"><title>Legislators</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:24px 16px;color:#1b2330}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}} .c{{border:1px solid #e4e9f0;border-radius:8px;padding:10px}}
.img{{height:150px;display:grid;place-items:center;background:#f4f7fb;border-radius:6px;margin-bottom:6px;overflow:hidden}} .img img{{max-height:150px;max-width:100%}}
.m{{font-size:12.5px;color:#4b5566}}</style>
<h1>Legislators</h1><p>{len(rows)} members. Check names, parties and photos. To fix a row, edit <code>content/legislators/members.csv</code>
and set its <code>source</code> to <code>manual</code> so the next fetch keeps your change.</p><div class="g">{cards}</div>''', encoding='utf-8')


def main(argv=None, log=print):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true', help='download every photo again')
    ap.add_argument('--chamber', choices=['House', 'Senate'], help='just one chamber')
    a = ap.parse_args(argv)
    cfg = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
    csv_path = ROOT / cfg.get('legislators', {}).get('file', 'content/legislators/members.csv')
    photo_dir = csv_path.parent / 'photos'
    photo_dir.mkdir(parents=True, exist_ok=True)
    old = {}
    if csv_path.exists():
        with open(csv_path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                old[(r['chamber'], r['name'])] = r
    rows, problems = [], []
    for chamber in ([a.chamber] if a.chamber else ['House', 'Senate']):
        try:
            members = read_list(chamber, log)
        except Exception as e:  # noqa: BLE001
            problems.append(f'{chamber} list: {e}')
            log(f'Could not read the {chamber} list: {e}')
            continue
        for i, m in enumerate(members, 1):
            prev = old.get((chamber, m['name']))
            if prev and prev.get('source') == 'manual':
                rows.append(prev)
                continue
            row = {'chamber': chamber, 'district': m['district'], 'name': m['name'], 'party': m['party'], 'url': m['url'], 'photo': (prev or {}).get('photo', ''), 'source': 'ilga.gov'}
            try:
                photo_url = read_member(m, log)
                row['party'], row['district'] = m['party'], m['district']
                have = row['photo'] and (photo_dir / row['photo']).exists()
                if photo_url and (a.force or not have):
                    row['photo'] = save_photo(photo_url, photo_dir / f'{chamber.lower()}-{slugify(m["name"])}').name
                elif not photo_url and not have:
                    problems.append(f'{chamber} {m["name"]}: no photo found on {m["url"]}')
            except Exception as e:  # noqa: BLE001 - keep going; report at the end
                problems.append(f'{chamber} {m["name"]}: {e}')
            log(f'  {i}/{len(members)} {m["name"]} ({row["party"][:1] or "?"}-{row["district"] or "?"}){" photo" if row["photo"] else ""}')
            rows.append(row)
            time.sleep(0.3)  # be polite to the server
    if not rows:
        log('Nothing collected. Check the internet connection, or whether ilga.gov changed its member pages.')
        return 1
    kept = [r for k, r in old.items() if k[0] not in {x['chamber'] for x in rows}]  # chambers not fetched this time
    rows = sorted(rows + kept, key=lambda r: (r['chamber'], int(r['district'] or 999), r['name']))
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    out = ROOT / cfg['build']['output']
    out.mkdir(parents=True, exist_ok=True)
    review_page(rows, out / 'legislators.html', photo_dir)
    log(f'Wrote {csv_path.relative_to(ROOT)}: {len(rows)} legislators, {sum(1 for r in rows if r.get("photo"))} with photos, '
        f'{sum(1 for r in rows if not r.get("party"))} without a party.')
    for p in problems:
        log('  check: ' + p)
    log(f'Review them in {(out / "legislators.html").relative_to(ROOT)}, then Check data and Publish.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
