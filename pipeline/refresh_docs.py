#!/usr/bin/env python3
"""Update the published site's page and text without rebuilding the data.

    python pipeline/refresh_docs.py

For changes to the site's design (site/index.html) or its content (content/home/home.toml,
content/thinktanks/profiles.toml, logos and photos) when the data hasn't changed. It copies the page into docs/,
rebuilds docs/data/home.json and orgs.json and their images from content/, and refreshes their version stamps in
docs/data/manifest.json. Bills, speeches and reports in docs/ are left exactly as last published from the lab.
"""
import json, shutil, sys, tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as B  # noqa: E402

ROOT = B.ROOT


def main():
    cfg = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
    docs = ROOT / 'docs'
    d = docs / 'data'
    if not (d / 'manifest.json').exists():
        sys.exit('docs/ has no published site yet. Publish once from the lab (Update website → Publish) first.')
    rep = B.Report()
    tt = json.loads((d / 'thinktanks.json').read_text(encoding='utf-8'))['rows']
    profiles, copies = B.build_profiles(cfg, tt, rep)
    home, home_media = B.build_home(cfg, rep)
    for sub in ('logos', 'site'):
        if (d / sub).exists():
            shutil.rmtree(d / sub)
    for src, dest in list(copies) + list(home_media):
        (d / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / dest)
    man = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    man['v']['orgs'] = B.dump(d / 'orgs.json', profiles or {})
    if home:
        man['v']['home'] = B.dump(d / 'home.json', home)
    B.dump(d / 'manifest.json', man)
    shutil.copyfile(ROOT / 'site' / 'index.html', docs / 'index.html')
    (docs / '.nojekyll').write_text('')
    for level, ds, msg, _ in rep.items:
        print(f'  [{level}] {ds}: {msg}')
    print('docs/ updated: page and content refreshed; data unchanged.')


if __name__ == '__main__':
    main()
