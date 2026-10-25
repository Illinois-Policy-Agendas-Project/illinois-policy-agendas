#!/usr/bin/env python3
"""Collect each think tank's logo from its own website.

    python pipeline/fetch_logos.py            fetch logos that are missing
    python pipeline/fetch_logos.py --force    fetch again, replacing existing logos
    python pipeline/fetch_logos.py --only "Delta Institute"

For every organization in content/thinktanks/profiles.toml with a `website`, it reads the home page and picks,
in order of preference: an image marked as the logo (by file name, alt text, class or id; SVG first),
the site's touch icon, its social-sharing image, then its favicon. The file is saved in
content/thinktanks/logos/ and the `logo` line in profiles.toml is updated to match.
Then it writes build/logos.html so someone can check every logo before publishing.
Use logos with each organization's permission.
"""
import argparse, html, re, sys, tomllib, urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
UA = 'Mozilla/5.0 (IllinoisPolicyAgendas logo collector; research website)'
TYPES = {'image/svg+xml': '.svg', 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/x-icon': '.ico', 'image/vnd.microsoft.icon': '.ico'}


class Candidates(HTMLParser):
    """Collects (score, url, why) for anything on a page that looks like the site's logo."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.found, self.in_header = base, [], 0

    def add(self, score, url, why):
        if url and not url.startswith('data:'):
            self.found.append((score, urljoin(self.base, url.strip()), why))

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag in ('header', 'nav'):
            self.in_header += 1
        if tag == 'img':
            src = a.get('src') or a.get('data-src') or (a.get('srcset', '').split(' ')[0])
            text = ' '.join([src, a.get('alt', ''), a.get('class', ''), a.get('id', '')]).lower()
            if 'logo' in text:
                score = 100 + (20 if src.lower().split('?')[0].endswith('.svg') else 0) + (10 if self.in_header else 0) - (30 if 'footer' in text else 0)
                self.add(score, src, 'logo image on the home page')
        elif tag == 'link':
            rel = a.get('rel', '').lower()
            if 'apple-touch-icon' in rel:
                size = int((re.findall(r'\d+', a.get('sizes', '')) or [180])[0])
                self.add(60 + min(size, 512) / 32, a.get('href'), 'touch icon')
            elif 'icon' in rel:
                href = a.get('href', '')
                self.add(20 + (15 if href.lower().endswith(('.svg', '.png')) else 0), href, 'site icon')
        elif tag == 'meta' and (a.get('property') or a.get('name', '')).lower() in ('og:image', 'twitter:image'):
            self.add(40, a.get('content'), 'social-sharing image')

    def handle_endtag(self, tag):
        if tag in ('header', 'nav') and self.in_header:
            self.in_header -= 1


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get('Content-Type', '').split(';')[0].strip().lower(), r.geturl()


def find_logo(website, log):
    page, _, final = get(website)
    p = Candidates(final)
    p.feed(page.decode('utf-8', 'replace'))
    cands = sorted(p.found, key=lambda c: -c[0])
    cands.append((0, urljoin(final, '/favicon.ico'), 'favicon'))
    seen = set()
    for score, url, why in cands:
        if url in seen:
            continue
        seen.add(url)
        try:
            data, ctype, _ = get(url)
        except Exception as e:  # noqa: BLE001 - try the next candidate
            log(f'    skipped {url} ({e})')
            continue
        ext = TYPES.get(ctype) or Path(urlparse(url).path).suffix.lower()
        if ext == '.svg' or data.lstrip()[:5] in (b'<?xml', b'<svg ') or b'<svg' in data[:400]:
            ext = '.svg'
        if ext not in ('.svg', '.png', '.jpg', '.jpeg', '.webp'):
            log(f'    skipped {url} (not a usable image type: {ctype or ext or "unknown"})')
            continue
        if len(data) < 300:
            log(f'    skipped {url} (too small to be a logo)')
            continue
        return data, ('.jpg' if ext == '.jpeg' else ext), url, why
    return None


def set_logo_line(toml_text, name, filename):
    """Point `logo` at the new file inside the [[org]] block whose name matches."""
    blocks = toml_text.split('[[org]]')
    for i, b in enumerate(blocks):
        if re.search(r'^name\s*=\s*"' + re.escape(name) + r'"\s*$', b, re.M):
            if re.search(r'^logo\s*=', b, re.M):
                b = re.sub(r'^logo\s*=.*$', f'logo = "{filename}"', b, count=1, flags=re.M)
            else:
                b = re.sub(r'^(name\s*=.*)$', rf'\1\nlogo = "{filename}"', b, count=1, flags=re.M)
            blocks[i] = b
    return '[[org]]'.join(blocks)


def review_page(entries, path):
    rows = ''.join(f'''<div class="c"><div class="img">{f'<img src="{html.escape(e["rel"])}" alt="">' if e.get("rel") else '<span>no logo</span>'}</div>
      <b>{html.escape(e["name"])}</b><div class="m">{html.escape(e["note"])}</div>{f'<div class="m">from <a href="{html.escape(e["src"])}">{html.escape(e["src"])}</a></div>' if e.get("src") else ''}</div>''' for e in entries)
    path.write_text(f'''<!doctype html><meta charset="utf-8"><title>Think tank logos</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:24px 16px;color:#1b2330}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}} .c{{border:1px solid #e4e9f0;border-radius:8px;padding:12px}}
.img{{height:90px;display:grid;place-items:center;background:#fff;border:1px dashed #e4e9f0;border-radius:6px;margin-bottom:8px}} .img img{{max-width:90%;max-height:80px}}
.m{{font-size:12.5px;color:#4b5566;word-break:break-all}}</style>
<h1>Think tank logos</h1><p>Check that each image is the organization's current logo and reads well on white. To replace one, save a better file in
<code>content/thinktanks/logos/</code> and update its <code>logo</code> line in <code>profiles.toml</code>.</p><div class="g">{rows}</div>''', encoding='utf-8')


def main(argv=None, log=print):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--force', action='store_true', help='replace logos that are already there')
    ap.add_argument('--only', help='just this organization (its name as in profiles.toml)')
    a = ap.parse_args(argv)
    cfg = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
    prof_path = ROOT / cfg['thinktanks']['profiles']
    text = prof_path.read_text(encoding='utf-8')
    orgs = tomllib.loads(text).get('org', [])
    logo_dir = prof_path.parent / 'logos'
    logo_dir.mkdir(parents=True, exist_ok=True)
    out = ROOT / cfg['build']['output']
    out.mkdir(parents=True, exist_ok=True)
    entries, got, failed = [], 0, 0
    for o in orgs:
        name = o.get('name', '')
        if a.only and name != a.only:
            continue
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        current = logo_dir / o['logo'] if o.get('logo') else None
        if current and current.exists() and not a.force:
            log(f'{name}: already has {current.name}; skipping (use --force to replace)')
            entries.append({'name': name, 'rel': f'../{current.relative_to(ROOT).as_posix()}', 'note': 'existing file'})
            continue
        if not o.get('website'):
            log(f'{name}: no website in profiles.toml; skipping')
            entries.append({'name': name, 'note': 'no website listed'})
            continue
        log(f'{name}: looking at {o["website"]}')
        try:
            found = find_logo(o['website'], log)
        except Exception as e:  # noqa: BLE001
            found = None
            log(f'    could not open the website ({e})')
        if not found:
            failed += 1
            entries.append({'name': name, 'note': 'not found: add a logo by hand'})
            log(f'  -> no logo found. Save one by hand as content/thinktanks/logos/{slug}.png')
            continue
        data, ext, src, why = found
        fname = slug + ext
        for old in logo_dir.glob(slug + '.*'):
            if old.name != fname:
                old.unlink()
        (logo_dir / fname).write_bytes(data)
        text = set_logo_line(text, name, fname)
        got += 1
        entries.append({'name': name, 'rel': f'../{(logo_dir / fname).relative_to(ROOT).as_posix()}', 'src': src, 'note': f'{why}, {len(data) // 1000 or 1} KB'})
        log(f'  -> saved {fname} ({why})')
    prof_path.write_text(text, encoding='utf-8')
    review_page(entries, out / 'logos.html')
    log(f'\nSaved {got} logo(s); {failed} not found. Check them at build/logos.html (or "Review logos" in the control panel), then run Check data.')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
