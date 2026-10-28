#!/usr/bin/env python3
"""A short Markdown summary of the last check (build/status.json), for the run's page on GitHub ($GITHUB_STEP_SUMMARY)."""
import json, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cfg = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8'))
st = ROOT / cfg['build']['output'] / 'status.json'
if not st.exists():
    print('## The check did not run\n\nSee the log of the "Check data" step.')
    sys.exit()
s = json.loads(st.read_text())
head = 'Ready to publish' if s['ok'] else f"{s['errors']} error(s): not published"
print(f'## {head}\n')
print(f"Checked {s['built']}. The full report is attached to this run (Artifacts → report).\n")
items = s.get('items', [])
for level, title in (('error', 'Errors (these block publishing)'), ('warning', 'Warnings')):
    rows = [i for i in items if i[0] == level]
    if rows:
        print(f'### {title}\n')
        print('\n'.join(f'- **{ds}:** {msg}' for _, ds, msg in rows) + '\n')
t = s.get('todo') or {}
if t:
    unc = ', '.join(f'{n:,} in the {ga}th GA' for ga, n in (t.get('uncoded') or {}).items())
    print(f"### Coding\n\n- Bills without a code: {unc or 'none'}\n- New think tank reports waiting for a code: {t.get('reports_waiting', 0):,}")
