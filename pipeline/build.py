#!/usr/bin/env python3
"""Build the website's data from the coding files, check it, and write a report.

    python pipeline/build.py                   check + build into build/site, write build/report.html
    python pipeline/build.py --worksheet 104   also write a coding worksheet of uncoded bills for session 104

Exit code 0 = ready to publish, 1 = errors found (see the report), 2 = could not run.
"""
import argparse, csv, datetime as dt, hashlib, html, json, os, re, shutil, sys, tomllib
from collections import Counter, defaultdict
from pathlib import Path

import ftfy
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent          # this repository: code, site template, content, build output
# The project's data (bill scrapes, coding files, speeches, reports, subtopic index) stays in Dropbox. The launcher
# sets IL_AGENDAS_DATA to the Dropbox Website folder; paths for data in config.toml are relative to it.
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
STATE = DATA / 'state'                                   # shared across lab computers: what was last published


def show(p):
    """A short path for messages: relative to the data folder or the repository when possible."""
    p = Path(p)
    for base in (DATA, ROOT):
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(p)
NOPOL = -555


# ------------------------------------------------------------------ report
class Report:
    def __init__(self):
        self.items = []          # (level, dataset, message, examples)
        self.stats = {}
        self.changes = {}

    def add(self, level, ds, msg, examples=()):
        self.items.append((level, ds, msg, list(examples)[:12]))

    error = lambda self, ds, msg, ex=(): self.add('error', ds, msg, ex)
    warn = lambda self, ds, msg, ex=(): self.add('warning', ds, msg, ex)
    note = lambda self, ds, msg, ex=(): self.add('note', ds, msg, ex)

    @property
    def errors(self):
        return [i for i in self.items if i[0] == 'error']


# ------------------------------------------------------------------ helpers
def fix_text(s):
    """Repair mojibake (including double-encoded UTF-8 and Mac Roman), HTML entities and whitespace."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s)
    if '&' in s and ';' in s:
        s = html.unescape(s)
    if '¬' in s:  # UTF-8 bytes read as Mac Roman, then cp1252
        try:
            s = s.encode('mac_roman').decode('utf-8').encode('latin-1').decode('cp1252')
        except (UnicodeError, ValueError):
            pass
    for _ in range(6):
        n = ftfy.fix_text(s, normalization='NFC', unescape_html=False)
        if n == s:
            break
        s = n
    return re.sub(r'\s+', ' ', s).strip()


def read_table(path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() in ('.xlsx', '.xls', '.xlsm'):
        return pd.read_excel(path)
    return pd.read_csv(path, low_memory=False, encoding_errors='replace')


def to_code(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 'bad:' + str(v)


def to_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):  # Excel serial
        return (pd.Timestamp('1899-12-30') + pd.Timedelta(days=int(v))).strftime('%Y-%m-%d')
    try:
        return pd.Timestamp(v).strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def norm_bill(s):
    """'H B 3852' / 'hb3852' / 'HB 03852' -> ('HB', 'HB 3852')"""
    t = re.sub(r'\s+', '', str(s)).upper()
    m = re.match(r'^([A-Z]+)0*(\d+)$', t)
    return (m.group(1), f'{m.group(1)} {m.group(2)}') if m else (None, None)


def pl(n, one, many=None):
    """pl(1, 'bill') -> '1 bill'; pl(3, 'bill') -> '3 bills'"""
    return f'{n:,} {one if n == 1 else (many or one + "s")}'


def flag(v):
    try:
        return int(float(v)) == 1
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------------ codebook
def load_codebook(cfg, rep):
    """Topic labels and the list of valid codes, from the project's Illinois subtopic index.
    Column names come from [codebook] columns in config.toml; "" matches a column with a blank header."""
    cb = cfg['codebook']
    path = DATA / cb['file']
    col = cb.get('columns', {'label': 'label', 'code': 'code', 'major': 'major', 'major_label': 'major_label'})
    subs, majors, labels_seen, problems = {}, {}, defaultdict(list), []
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        rep.error('Codebook', f'Subtopic index not found: {cb["file"]}. Check [codebook] in config.toml.')
        return {'subs': {}, 'majors': {}}, {NOPOL}
    head = [h.strip() for h in rows[0]]
    try:
        idx = {k: head.index(v) for k, v in col.items()}
    except ValueError as e:
        rep.error('Codebook', f'The subtopic index is missing a column ({e}). Its header is: {", ".join(h or "(blank)" for h in head)}')
        return {'subs': {}, 'majors': {}}, {NOPOL}
    for n, row in enumerate(rows[1:], 2):
        if not any(x.strip() for x in row):
            continue
        try:
            c, m = int(float(row[idx['code']])), int(float(row[idx['major']]))
        except (ValueError, IndexError):
            problems.append(f'row {n}: {row}')
            continue
        if c == NOPOL:
            continue
        label = re.sub(r'\s+', ' ', row[idx['label']]).strip()
        if c in subs:
            problems.append(f'code {c} is listed twice')
        subs[c] = label
        majors[m] = re.sub(r'\s+', ' ', row[idx['major_label']]).strip()
        labels_seen[(m, label)].append(c)
    dup = [f'{" and ".join(map(str, cs))}: "{lab}"' for (m, lab), cs in labels_seen.items() if len(cs) > 1]
    if problems:
        rep.warn('Codebook', f'{pl(len(problems), "row")} of the subtopic index could not be read.', problems)
    if dup:
        rep.warn('Codebook', f'{pl(len(dup), "label is", "labels are")} used for more than one subtopic code, so the site shows two identical names. Edit {path.name} to tell them apart.', dup)
    valid = set(subs) | set(cb.get('extra_codes', [])) | {NOPOL}
    rep.stats['Codebook'] = {'records': len(subs)}
    return {'subs': subs, 'majors': majors}, valid


def load_governors(cfg, sots, rep):
    """Year -> governor and party, from the project's governor metadata. Checked against the speeches."""
    g = cfg.get('governors')
    if not g:
        return []
    try:
        df = read_table(DATA / g['file'])
    except Exception as e:  # noqa: BLE001
        rep.warn('Governors', f'Could not read {g["file"]}: {e}. Pages will show governors without party.')
        return []
    c = g.get('columns', {'year': 'year', 'governor': 'governor_name', 'party': 'party'})
    out = [{'year': int(r[c['year']]), 'governor': fix_text(r[c['governor']]), 'party': fix_text(r[c['party']])} for r in df.to_dict('records') if pd.notna(r[c['year']])]
    by = {x['year']: x['governor'] for x in out}
    mismatch = sorted({f'{y}: speech says {gname}, metadata says {by.get(y, "nothing")}' for _, gname, y, _, _ in sots if by.get(y) != gname})
    if mismatch:
        rep.warn('Governors', f'{pl(len(mismatch), "speech year does", "speech years do")} not match the governor metadata.', mismatch)
    return sorted(out, key=lambda x: x['year'])


def check_code(code, valid, rep, ds, where, bad):
    if code is None or code == NOPOL:
        return code
    if isinstance(code, str) or code not in valid:
        bad.append(f'{where}: {code}')
        return None
    return code


# ------------------------------------------------------------------ sponsors
SUFFIX = re.compile(r'^(Jr\.?|Sr\.?|II|III|IV)$')


class SponsorParser:
    """Turns ILGA sponsor strings into (chamber, name, role) triples.
    Roles: C chief sponsor, H chief co-sponsor, O co-sponsor, S/T sponsor/co-sponsor in the second chamber."""

    def __init__(self, all_strings):
        self.known = set()
        for s in all_strings:
            for g in self._groups(s):
                m = re.match(r'^(Rep|Sen)\.\s*(.*)$', g)
                if not m:
                    continue
                parts = [x.strip() for x in re.split(r'\s+-\s+|,\s*|\s+and\s+', self._norm(m.group(2))) if x.strip()]
                self.known |= {x for x in parts if '-' not in x}
                if len(parts) == 1:
                    self.known.add(parts[0])

    @staticmethod
    def _groups(s):
        if 'Sponsors;' in s:
            return [seg.strip().strip('()').strip() for seg in s.split(';') if re.match(r'^\(?(Rep|Sen)\.', seg.strip())]
        extra = re.findall(r'\(((?:Rep|Sen)\.[^)]*)\)', s)
        return [re.sub(r',?\s*\([^)]*\)', '', s).strip().rstrip(',')] + extra

    @staticmethod
    def _norm(txt):
        return re.sub(r'(,\s*(?:Jr\.?|Sr\.?|II|III|IV))-', r'\1 - ', txt)

    def _seg(self, segs):
        best = None

        def rec(i, acc, score):
            nonlocal best
            if i == len(segs):
                if best is None or score > best[0]:
                    best = (score, acc)
                return
            for j in range(i + 1, len(segs) + 1):
                nm = '-'.join(segs[i:j]).strip()
                rec(j, acc + [nm], score + (3 if nm in self.known else 0 if ' ' in nm else -5))
        rec(0, [], 0)
        return best[1]

    def _names(self, txt):
        out = []
        for chunk in re.split(r'\s+-\s+', self._norm(txt)):
            names = []
            for p in (p.strip() for p in re.split(r',\s*|\s+and\s+', chunk) if p.strip()):
                if SUFFIX.match(p) and names:
                    names[-1] += ', ' + p
                elif '-' in p:
                    names.extend(self._seg(p.split('-')))
                else:
                    names.append(p)
            out.append(names)
        return out

    def parse(self, s):
        if not s:
            return []
        res = []
        for gi, g in enumerate(self._groups(s)):
            m = re.match(r'^(Rep|Sen)\.\s*(.*)$', g)
            if not m:
                continue
            ch, chunks, first = m.group(1), self._names(m.group(2)), True
            for ci, names in enumerate(chunks):
                for ni, n in enumerate(names):
                    if re.match(r'^All ', n):
                        continue
                    if gi == 0:
                        role = 'C' if first else ('H' if ci > 0 and (ni == 0 or ci < len(chunks) - 1) else 'O')
                    else:
                        role = 'S' if first else 'T'
                    res.append((ch, n, role))
                    first = False
        return res


def canonical_names(raw_triples):
    names = {(ch, n) for ch, n, _ in raw_triples}

    def canon(ch, n):
        n2 = re.sub(r'\b([A-Z])(?=\s)', r'\1.', n)
        if (ch, n2) in names:
            n = n2
        for suf in (', Jr.', ', Sr.', ', II', ', III'):
            if (ch, n + suf) in names:
                return n + suf
        hy = [m for c, m in names if c == ch and m.startswith(n + '-')]
        return hy[0] if len(hy) == 1 else n
    return canon


# ------------------------------------------------------------------ bills
def build_bills(cfg, valid, rep):
    col = cfg['bills']['columns']
    ccol = cfg['bills'].get('code_columns', {'bill': 'bill', 'subtopic': 'subtopic'})
    sessions, all_sponsor_strings = [], []

    for sess in cfg['bills']['sessions']:
        ga = int(sess['ga'])
        ds = f'Bills, {ga}th GA'
        if sess.get('status') == 'upcoming':
            if not (DATA / sess['details']).exists():
                rep.note(ds, f'Placeholder for the {ga}th General Assembly ({sess.get("years", "")}): no bill file yet at {sess["details"]}. '
                             'The site lists the session as not started. It goes live by itself once the scraper saves that file.')
                sessions.append({'ga': ga, 'years': sess.get('years', ''), 'starts': sess.get('starts', ''), 'bills': {}, 'upcoming': True})
                continue
            rep.note(ds, f'The bill file for the {ga}th General Assembly is in place, so the session is now live. You can delete `status = "upcoming"` from config.toml.')
        try:
            df = read_table(DATA / sess['details'])
        except FileNotFoundError as e:
            rep.error(ds, f'Bill file not found: {e}. Check `details` in config.toml.')
            continue
        except Exception as e:  # noqa: BLE001 - report any unreadable file
            rep.error(ds, f'Could not read {sess["details"]}: {e}')
            continue
        missing = [c for k, c in col.items() if c not in df.columns and k not in ('actions', 'roll_call', 'nomination', 'appropriation', 'commemorative', 'chamber')]
        if missing:
            rep.error(ds, f'Missing columns in {Path(sess["details"]).name}: {", ".join(missing)}. Update [bills.columns] in config.toml if the scraper renamed them.')
            continue

        bills, bad_codes, unparsed, excluded_keys = {}, [], [], set()
        for i, r in enumerate(df.to_dict('records')):
            typ, num = norm_bill(r.get(col['bill']))
            if not num:
                unparsed.append(f'row {i + 2}: {r.get(col["bill"])!r}')
                continue
            link = str(r.get(col['link']) or '')
            stem = re.search(r'/HTML/([^/]+)\.html$', link)
            rec = {
                'ga': ga, 'bill': num, 'type': typ,
                'chamber': (str(r.get(col.get('chamber', ''), '') or '').split(';')[0].strip() or ('House' if typ.startswith('H') else 'Senate' if typ.startswith('S') else '')),
                'short': fix_text(r.get(col['short'])) or '',
                'sponsors_raw': fix_text(r.get(col['sponsors'])) or '',
                'intro': to_date(r.get(col['introduced'])),
                'last_date': to_date(r.get(col['last_action_date'])),
                'last_action': fix_text(r.get(col['last_action'])) or '',
                'actions_raw': fix_text(r.get(col.get('actions', ''), '')) or '',
                'synopsis': fix_text(r.get(col['synopsis'])) or '',
                'sub': check_code(to_code(r.get(col['subtopic'])), valid, rep, ds, num, bad_codes),
                'flags': ''.join(k for k, c in zip('NACLR', ['nomination', 'appropriation', 'commemorative', 'became_law', 'roll_call']) if flag(r.get(col.get(c, ''), 0))),
                'link': stem.group(1) if stem and f'/legislation/{ga}/' in link else link,
                'source': 'details',
            }
            key = f'{ga}-{num.replace(" ", "")}'
            if key in bills:  # same bill twice: keep the most recent snapshot
                old = bills[key]
                rep_dupes = rep.stats.setdefault(f'dupes_{ga}', [])
                rep_dupes.append(num)
                if (rec['last_date'] or '') < (old['last_date'] or '') or ((rec['last_date'] == old['last_date']) and len(rec['actions_raw']) < len(old['actions_raw'])):
                    continue
            bills[key] = rec

        # keep substantive bills only (config [bills] include_types / exclude_flags)
        keep_types = set(cfg['bills'].get('include_types', []))
        drop_flags = set(cfg['bills'].get('exclude_flags', []))
        excluded = Counter()
        if keep_types or drop_flags:
            for key in list(bills):
                b = bills[key]
                why = (b['type'] if keep_types and b['type'] not in keep_types else
                       'commemorative' if 'C' in b['flags'] and 'C' in drop_flags else 'nomination' if 'N' in b['flags'] and 'N' in drop_flags else None)
                if why:
                    excluded[why] += 1
                    del bills[key]
                    excluded_keys.add(key)
            if excluded:
                rep.note(ds, f'Kept {pl(len(bills), "substantive bill")}; left out {pl(sum(excluded.values()), "other measure")} ({", ".join(f"{n:,} {k}" for k, n in excluded.most_common())}).')
        if unparsed:
            rep.warn(ds, f'{pl(len(unparsed), "row has", "rows have")} a bill number the build cannot read and {"was" if len(unparsed) == 1 else "were"} skipped.', unparsed)
        dup = rep.stats.pop(f'dupes_{ga}', [])
        if dup:
            rep.warn(ds, f'{pl(len(dup), "bill appears", "bills appear")} more than once in the bill file. The site uses the most recent row of each.', sorted(set(dup)))

        # coders' files override codes in the details file
        cdir = DATA / sess.get('codes', '') if sess.get('codes') else None
        if cdir and cdir.exists():
            seen, conflicts, unknown, bad, n_files, applied, changed, disputed = {}, [], [], [], 0, 0, 0, set()
            for f in sorted(p for p in cdir.iterdir() if p.suffix.lower() in ('.xlsx', '.xls', '.csv') and not p.name.startswith(('~$', '.'))):
                try:
                    cdf = read_table(f)
                except Exception as e:  # noqa: BLE001
                    rep.error(ds, f'Could not read coding file {f.name}: {e}')
                    continue
                if ccol['bill'] not in cdf.columns or ccol['subtopic'] not in cdf.columns:
                    rep.error(ds, f'Coding file {f.name} needs columns "{ccol["bill"]}" and "{ccol["subtopic"]}".')
                    continue
                n_files += 1
                for i, r in enumerate(cdf.to_dict('records')):
                    code = to_code(r.get(ccol['subtopic']))
                    if code is None:
                        continue  # not coded yet
                    _, num = norm_bill(r.get(ccol['bill']))
                    where = f'{f.name} row {i + 2} ({r.get(ccol["bill"])})'
                    if not num:
                        bad.append(where + ': bill number unreadable')
                        continue
                    if code != NOPOL and (isinstance(code, str) or code not in valid):
                        bad.append(f'{where}: {code}')
                        continue
                    key = f'{ga}-{num.replace(" ", "")}'
                    if key in seen and seen[key][0] != code:
                        conflicts.append(f'{num}: {seen[key][0]} in {seen[key][1]}, {code} in {f.name}')
                        disputed.add(key)
                        continue
                    seen[key] = (code, f.name)
            for key in disputed:
                seen.pop(key, None)
            for key, (code, fname) in seen.items():
                if key in excluded_keys:
                    continue  # a resolution or other measure that is not a substantive bill
                if key not in bills:
                    unknown.append(f'{key.split("-", 1)[1]} ({fname})')
                    continue
                if bills[key]['sub'] != code:
                    changed += bills[key]['sub'] is not None
                    bills[key]['sub'] = code
                bills[key]['source'] = 'codes'
                applied += 1
            for k in disputed:
                if k in bills:
                    bills[k]['sub'] = None
            rep.note(ds, f'Read {pl(n_files, "coding file")}: {pl(applied, "code")} applied, {changed:,} of them replacing a code from the bill file.')
            if bad:
                rep.error(ds, f'{pl(len(bad), "code")} in the coding files {"is" if len(bad) == 1 else "are"} not in the codebook or {"has" if len(bad) == 1 else "have"} an unreadable bill number.', bad)
            if conflicts:
                rep.error(ds, f'{pl(len(conflicts), "bill has", "bills have")} different codes in different coding files. {"It shows" if len(conflicts) == 1 else "They show"} as "Not yet coded" until the coders agree.', conflicts)
            if unknown:
                rep.warn(ds, f'{pl(len(unknown), "coded bill is", "coded bills are")} not in the bill file (a typo in the bill number, or the scraper has not picked {"it" if len(unknown) == 1 else "them"} up yet).', unknown)
        elif cdir:
            rep.note(ds, f'No coding folder at {sess["codes"]} yet; using codes from the bill file only.')

        # a bad code in the bill file only matters if no coding file has replaced it
        bad_codes = [b for b in bad_codes if bills.get(f'{ga}-{b.split(":")[0].replace(" ", "")}', {}).get('source') != 'codes']
        if bad_codes:
            rep.error(ds, f'{pl(len(bad_codes), "bill")} in the bill file {"has a code" if len(bad_codes) == 1 else "have codes"} not in the codebook. {"It shows" if len(bad_codes) == 1 else "They show"} as "Not yet coded" until fixed, or until a coding file gives {"it" if len(bad_codes) == 1 else "them"} a code.', bad_codes)

        n = len(bills)
        coded = sum(1 for b in bills.values() if b['sub'] not in (None,))
        rep.stats[ds] = {'records': n, 'coded': coded, 'uncoded': n - coded}
        all_sponsor_strings += [b['sponsors_raw'] for b in bills.values() if b['sponsors_raw']]
        sessions.append({'ga': ga, 'years': sess.get('years', ''), 'bills': bills})

    # sponsors across all sessions
    sp = SponsorParser(all_sponsor_strings)
    raw = {}
    for s in sessions:
        for key, b in s['bills'].items():
            raw[key] = sp.parse(b['sponsors_raw'])
    canon = canonical_names([t for l in raw.values() for t in l])
    people, links = {}, {}
    for key, lst in raw.items():
        out, seen = [], set()
        for ch, n, role in lst:
            pk = (ch, canon(ch, n))
            if pk in seen:
                continue
            seen.add(pk)
            people.setdefault(pk, len(people))
            out.append([people[pk], role])
        links[key] = out
    no_sponsor = [k for k, l in links.items() if not l]
    if no_sponsor:
        rep.note('Sponsors', f'{pl(len(no_sponsor), "measure lists", "measures list")} no sponsor (often executive orders).', no_sponsor)
    rep.stats['Sponsors'] = {'records': len(people)}
    return sessions, [list(k) for k in sorted(people, key=people.get)], links


def parse_actions(raw, last_action):
    a = [x.strip() for x in raw.split('; ') if x.strip()]
    if len(a) > 1 and a[0] == last_action:
        a = a[1:]
    out = []
    for x in a:
        if re.fullmatch(r'\d{3}-\d{3}-\d{3}', x) and out:
            out[-1] = [out[-1] if isinstance(out[-1], str) else out[-1][0], x]
        else:
            out.append(x)
    return out


STAGE_LAW = re.compile(r'^Public Act\b')
STAGE_BOTH = re.compile(r'Passed Both Houses|Adopted Both Houses')
STAGE_CHAMBER = re.compile(r'Third Reading.*Passed|^Resolution Adopted|^Appointment Confirmed')
STAGE_CMTE = re.compile(r'^(Do Pass|Recommends Be Adopted|Recommends Do Adopt|Recommends Do Consent|Reported Out Of Committee|Approved for Consideration|Be Adopted)', re.I)
CMTE_NAME = re.compile(r'^(?:Do Pass(?: as Amended)?(?: / (?:Short|Standard) Debate)?|Recommends Be Adopted(?: as Amended)?|Recommends Do (?:Adopt|Consent)|Approved for Consideration|Reported Out Of Committee to)\s*(.*)$', re.I)


def bill_stage(steps, last_action, last_date, origin=None):
    """Furthest stage a measure reached, from its ILGA action history:
    1 introduced, 2 out of committee, 3 passed one chamber, 4 passed both chambers, 5 became law (has a Public Act).
    Also returns the committee that first approved it, its Public Act number, the date it became law and whether it was vetoed."""
    texts = [s if isinstance(s, str) else s[0] for s in steps] + [last_action]
    stage, cmte, pa, law_date = 1, None, None, None
    chamber = origin  # 'House' or 'Senate'; follows the bill when it arrives in the other chamber
    for t in texts:
        m = re.match(r'^Arrived? in (?:the )?(House|Senate)', t)
        if m:
            chamber = m.group(1)
        if STAGE_CMTE.match(t):
            stage = max(stage, 2)
            if cmte is None:
                m = CMTE_NAME.match(t)
                name = (m.group(1) if m else '').strip(' -')
                if name and chamber:
                    cmte = f'{chamber} {name}'
                elif name:  # chamber unknown: House committee names end in "Committee", Senate ones don't
                    cmte = name if name.endswith('Committee') else f'Senate {name}' if not name.startswith('Senate') else name
                    if cmte.endswith('Committee') and not cmte.startswith('House'):
                        cmte = 'House ' + cmte
        elif re.match(r'^(Second Reading|Placed on Calendar (Order of )?(2nd|3rd) Reading)', t):
            stage = max(stage, 2)
        if STAGE_CHAMBER.search(t):
            stage = max(stage, 3)
        if STAGE_BOTH.search(t):
            stage = max(stage, 4)
        if STAGE_LAW.match(t):
            stage = 5
            m = re.search(r'(\d{2,3}-\d{4})', t)
            pa = pa or (m.group(1) if m else None)
    if stage == 5 and STAGE_LAW.match(last_action or ''):
        law_date = last_date
    veto = any(re.search(r'Vetoed|Total Veto|Amendatory Veto', t) for t in texts)
    return stage, cmte, pa, law_date, veto


# ------------------------------------------------------------------ speeches and reports
def build_sots(cfg, valid, rep):
    ds, c = 'State of the State', cfg['sots']['columns']
    try:
        df = read_table(DATA / cfg['sots']['file'])
    except Exception as e:  # noqa: BLE001
        rep.error(ds, f'Could not read {cfg["sots"]["file"]}: {e}')
        return []
    missing = [v for v in c.values() if v not in df.columns]
    if missing:
        rep.error(ds, f'Missing columns: {", ".join(missing)}')
        return []
    df = df.sort_values(c['id'])
    # a sentence with no year takes its speech's year: safe when the sentences on either side agree
    missing = df[c['year']].isna()
    before, after = df[c['year']].ffill(), df[c['year']].bfill()
    gov_b, gov_a = df[c['governor']].where(~missing).ffill(), df[c['governor']].where(~missing).bfill()
    sure = missing & (before == after) & (gov_b == df[c['governor']]) & (gov_a == df[c['governor']])
    unsure = missing & ~sure
    if sure.any():
        rep.note(ds, f'{pl(int(sure.sum()), "sentence has", "sentences have")} no year in the speech file; each sits inside one speech, so it was given that speech\'s year.', [f'id {i}' for i in df.loc[sure, c['id']]])
    if unsure.any():
        rep.warn(ds, f'{pl(int(unsure.sum()), "sentence has", "sentences have")} no year and {"falls" if unsure.sum() == 1 else "fall"} between two speeches; each was given the year of the sentence before it. Add the year in the speech file.', [f'id {i}' for i in df.loc[unsure, c['id']]])
    df[c['year']] = before
    bad, rows = [], []
    for r in df.to_dict('records'):
        code = check_code(to_code(r[c['subtopic']]), valid, rep, ds, f'id {r[c["id"]]}', bad)
        rows.append([int(r[c['id']]), fix_text(r[c['governor']]), int(r[c['year']]), fix_text(r[c['text']]) or '', code])
    if bad:
        rep.error(ds, f'{pl(len(bad), "sentence has a code", "sentences have codes")} not in the codebook.', bad)
    rep.stats[ds] = {'records': len(rows), 'coded': sum(1 for r in rows if r[4] is not None), 'uncoded': sum(1 for r in rows if r[4] is None)}
    return rows


def build_tt(cfg, valid, rep):
    ds, c = 'Think tank reports', cfg['thinktanks']['columns']
    try:
        df = read_table(DATA / cfg['thinktanks']['file'])
    except Exception as e:  # noqa: BLE001
        rep.error(ds, f'Could not read {cfg["thinktanks"]["file"]}: {e}')
        return []
    missing = [v for v in c.values() if v not in df.columns]
    if missing:
        rep.error(ds, f'Missing columns: {", ".join(missing)}')
        return []
    bad, rows, ids = [], [], Counter(df[c['id']])
    dup = [f'id {k}' for k, n in ids.items() if n > 1]
    if dup:
        rep.error(ds, f'{pl(len(dup), "report id is", "report ids are")} used more than once. Each report needs its own id because the id is its web address.', dup)
    for r in df.to_dict('records'):
        code = check_code(to_code(r[c['subtopic']]), valid, rep, ds, f'id {r[c["id"]]}', bad)
        y = r.get(c['year'])
        rows.append([int(r[c['id']]), fix_text(r[c['org']]), to_date(r.get(c['date'])), int(y) if pd.notna(y) else None,
                     fix_text(r[c['title']]) or '', fix_text(r.get(c['abstract'])) or '', fix_text(r.get(c['url'])), fix_text(r.get(c['pdf'])), code])
    if bad:
        rep.error(ds, f'{pl(len(bad), "report has a code", "reports have codes")} not in the codebook.', bad)
    undated = sum(1 for r in rows if r[3] is None)
    if undated:
        rep.note(ds, f'{pl(undated, "report has", "reports have")} no date.')
    rep.stats[ds] = {'records': len(rows), 'coded': sum(1 for r in rows if r[8] is not None), 'uncoded': sum(1 for r in rows if r[8] is None)}
    return rows


# ------------------------------------------------------------------ think tank profiles
LOGO_TYPES = {'.svg', '.png', '.jpg', '.jpeg', '.webp'}


def org_slug(name):
    """Same rule the website uses for #org-<slug> addresses."""
    import unicodedata
    s = unicodedata.normalize('NFD', name.lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def build_profiles(cfg, tt, rep):
    """Bios and logos for each think tank, from content/thinktanks/profiles.toml and its logos folder."""
    ds = 'Think tank profiles'
    path = cfg['thinktanks'].get('profiles')
    if not path:
        return {}, []
    path = ROOT / path
    if not path.exists():
        rep.note(ds, f'No profiles file at {show(path)}; organization pages will show no biography.')
        return {}, []
    try:
        entries = tomllib.loads(path.read_text(encoding='utf-8')).get('org', [])
    except Exception as e:  # noqa: BLE001
        rep.error(ds, f'Could not read {path.name}: {e}')
        return {}, []
    in_data = {r[1] for r in tt}
    logo_dir = path.parent / 'logos'
    profiles, copies, unknown, no_logo, bad_logo, unreviewed = {}, [], [], [], [], []
    for e in entries:
        name = (e.get('name') or '').strip()
        if name not in in_data:
            unknown.append(name or '(blank name)')
            continue
        p = {k: e[k] for k in ('official_name', 'founded', 'based', 'website', 'bio', 'sources') if e.get(k)}
        logo = e.get('logo')
        if logo:
            f = logo_dir / logo
            if not f.exists():
                no_logo.append(f'{name}: save it as {f.relative_to(ROOT)}')
            elif f.suffix.lower() not in LOGO_TYPES:
                bad_logo.append(f'{name}: {f.name} is not .svg, .png, .jpg or .webp')
            else:
                if f.stat().st_size > 400_000:
                    bad_logo.append(f'{name}: {f.name} is {f.stat().st_size // 1000} KB; under 200 KB loads faster')
                dest = f'logos/{org_slug(name)}{f.suffix.lower()}'
                copies.append((f, dest))
                p['logo'] = dest
        if not e.get('reviewed'):
            unreviewed.append(name)
        profiles[name] = p
    missing = sorted(in_data - set(profiles))
    if unknown:
        rep.warn(ds, f'{pl(len(unknown), "profile does", "profiles do")} not match an organization in the reports file (names must match exactly).', unknown)
    if missing:
        rep.note(ds, f'{pl(len(missing), "organization has", "organizations have")} no profile yet.', missing)
    if no_logo:
        rep.note(ds, f'{pl(len(no_logo), "logo is", "logos are")} not in the logos folder yet; those pages show a monogram.', no_logo)
    if bad_logo:
        rep.warn(ds, f'{pl(len(bad_logo), "logo file needs", "logo files need")} attention.', bad_logo)
    if unreviewed:
        rep.warn(ds, f'{pl(len(unreviewed), "biography has", "biographies have")} not been marked reviewed. Check the text, then set reviewed = true in {path.name}.', unreviewed)
    return profiles, copies


# ------------------------------------------------------------------ home page
def build_home(cfg, rep):
    """Home page text, sponsors and people from content/home/home.toml (see the comments in that file)."""
    ds, path = 'Home page', cfg.get('site', {}).get('home')
    if not path:
        return None, []
    path = ROOT / path
    if not path.exists():
        rep.warn(ds, f'No home page file at {show(path)}; the site opens on the Bills tab.')
        return None, []
    try:
        h = tomllib.loads(path.read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001
        rep.error(ds, f'Could not read {path.name}: {e}')
        return None, []
    copies, missing = [], []

    def media(folder, name, dest):
        if not name:
            return None
        f = path.parent / folder / name
        if not f.exists():
            missing.append(str(f.relative_to(ROOT)))
            return None
        if f.suffix.lower() not in LOGO_TYPES:
            rep.warn(ds, f'{f.name} is not .svg, .png, .jpg or .webp and was skipped.')
            return None
        copies.append((f, f'site/{dest}{f.suffix.lower()}'))
        return f'site/{dest}{f.suffix.lower()}'
    for s_ in h.get('sponsors', []):
        s_['logo'] = media('logos', s_.get('logo'), 'logo-' + org_slug(s_.get('name', 'sponsor')))
    for p in h.get('people', []):
        p['photo'] = media('photos', p.get('photo'), 'photo-' + org_slug(p.get('name', 'person')))
    if missing:
        rep.note(ds, f'{pl(len(missing), "image is", "images are")} not in place yet; the page shows text or initials instead.', missing)
    cb = (h.get('codebook') or '').strip()
    if cb.startswith(('http://', 'https://')):
        h['codebook_url'] = cb
    elif cb:
        f = path.parent / cb
        if not f.exists():
            rep.warn(ds, f'The codebook file {f.relative_to(ROOT)} named in {path.name} is missing; the home page shows "coming soon".')
        elif f.suffix.lower() not in ('.pdf', '.docx', '.xlsx', '.csv', '.html'):
            rep.warn(ds, f'{f.name}: the codebook should be a PDF, Word, Excel or CSV file.')
        else:
            copies.append((f, f'site/codebook{f.suffix.lower()}'))
            h['codebook_url'] = f'data/site/codebook{f.suffix.lower()}'
            if f.suffix.lower() == '.docx':  # a Word codebook also becomes the site's Codebook page
                try:
                    import codebook_doc
                    h['codebook_doc'] = codebook_doc.parse(f)
                    n = sum(len(m['subs']) for m in h['codebook_doc']['majors'])
                    rep.note(ds, f'Codebook page built from {f.name}: {len(h["codebook_doc"]["majors"])} major topics, {n} subtopics.')
                except Exception as e:  # noqa: BLE001
                    rep.warn(ds, f'Could not read {f.name} for the Codebook page ({e}); the home page links to the file instead.')
    else:
        rep.note(ds, 'No codebook yet; the home page shows "Codebook (coming soon)". Add it under `codebook` in home.toml.')
    h.pop('visualization', None)
    blank = [p.get('name', '?') for p in h.get('people', []) if not (p.get('bio') or '').strip()]
    if blank:
        rep.note(ds, f'{pl(len(blank), "person has", "people have")} no bio yet in {path.name}.', blank)
    if not h.get('reviewed'):
        rep.warn(ds, f'The home page text has not been marked reviewed. Check it, then set reviewed = true in {path.name}.')
    return h, copies


# ------------------------------------------------------------------ output
def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, separators=(',', ':'), ensure_ascii=False)
    path.write_text(data, encoding='utf-8')
    return hashlib.sha1(data.encode()).hexdigest()[:10]


def build_legislators(cfg, rep):
    """Party, district and photo for each legislator, from content/legislators/members.csv (made by fetch_legislators.py)."""
    ds = 'Legislators'
    path = ROOT / cfg.get('legislators', {}).get('file', 'content/legislators/members.csv')
    if not path.exists():
        rep.note(ds, f'No member list at {show(path)} yet; sponsor pages show no party or photo. Run "Fetch legislators" to collect them.')
        return [], []
    rows, copies, no_photo = [], [], 0
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            name, chamber = (r.get('name') or '').strip(), (r.get('chamber') or '').strip()
            if not name or chamber not in ('House', 'Senate'):
                continue
            photo = (r.get('photo') or '').strip()
            src = path.parent / 'photos' / photo if photo else None
            if src and src.exists():
                copies.append((src, f'legislators/{photo}'))
            else:
                no_photo += 1
                photo = ''
            rows.append({'ch': 'Rep' if chamber == 'House' else 'Sen', 'name': name, 'party': (r.get('party') or '').strip(),
                         'district': (r.get('district') or '').strip(), 'url': (r.get('url') or '').strip(), 'photo': f'legislators/{photo}' if photo else '',
                         'current': (r.get('source') or '').strip() != 'manual'})   # manual rows are mostly members who left; seats use current ones
    rep.note(ds, f'{len(rows)} legislators in {path.name}; {no_photo} without a photo, {sum(1 for r in rows if not r["party"])} without a party.')
    return rows, copies


def write_site(cfg, codebook, sessions, people, links, sots, tt, rep, profiles=None, logo_copies=(), governors=(), home=None, legislators=None):
    out = ROOT / cfg['build']['output'] / 'site'
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / cfg['build']['site_template'], out)
    d = out / 'data'
    files = {}
    files['codebook'] = dump(d / 'codebook.json', {'majors': codebook['majors'], 'subs': codebook['subs']})
    cols = ['key', 'ga', 'bill', 'type', 'chamber', 'short', 'intro', 'last_date', 'last_action', 'sub', 'flags', 'link', 'stage', 'cmte', 'pa', 'law_date', 'veto']
    for s in sessions:
        for b in s['bills'].values():
            b['stage'], b['cmte'], b['pa'], b['law_date'], b['veto'] = bill_stage(parse_actions(b['actions_raw'], b['last_action']), b['last_action'], b['last_date'], {'HB': 'House', 'SB': 'Senate'}.get(b['type']))
            b['veto'] = 1 if b['veto'] else 0
    rows = [[k] + [b[c] for c in cols[1:]] for s in sessions for k, b in sorted(s['bills'].items(), key=lambda kv: (kv[1]['intro'] or '', kv[0]))]
    files['bills'] = dump(d / 'bills' / 'summary.json', {'cols': cols, 'rows': rows})
    for s in sessions:
        ga = s['ga']
        if s.get('upcoming'):
            continue  # nothing to publish until the session has bills
        files[f'details-{ga}'] = dump(d / 'bills' / str(ga) / 'details.json', {k: b['synopsis'] for k, b in s['bills'].items()})
        files[f'actions-{ga}'] = dump(d / 'bills' / str(ga) / 'actions.json', {k: parse_actions(b['actions_raw'], b['last_action']) for k, b in s['bills'].items()})
        files[f'sponsors-{ga}'] = dump(d / 'bills' / str(ga) / 'sponsors.json', {k: links.get(k, []) for k in s['bills']})
    files['people'] = dump(d / 'people.json', {'people': people})
    files['sots'] = dump(d / 'sots.json', {'cols': ['id', 'governor', 'year', 'text', 'sub'], 'rows': sots})
    for src, dest in logo_copies:
        (d / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / dest)
    files['orgs'] = dump(d / 'orgs.json', profiles or {})
    if legislators:
        files['legislators'] = dump(d / 'legislators.json', legislators)
    files['governors'] = dump(d / 'governors.json', list(governors))
    if home:
        doc = home.pop('codebook_doc', None)
        if doc:
            files['codebook_doc'] = dump(d / 'codebook_doc.json', doc)
        files['home'] = dump(d / 'home.json', home)
    files['tt'] = dump(d / 'thinktanks.json', {'cols': ['id', 'org', 'date', 'year', 'title', 'abstract', 'url', 'pdf', 'sub'], 'rows': tt})
    manifest = {
        'title': cfg.get('title', ''), 'updated': dt.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'sessions': [{'ga': s['ga'], 'years': s['years'], 'bills': len(s['bills']), 'coded': sum(1 for b in s['bills'].values() if b['sub'] is not None),
                      **({'upcoming': True, 'starts': s.get('starts', '')} if s.get('upcoming') else {})} for s in sessions],
        'counts': {'sots': len(sots), 'tt': len(tt), 'people': len(people)},
        'changes': rep.changes, 'v': files,
    }
    dump(d / 'manifest.json', manifest)
    return out, manifest


def compare_with_last(cfg, sessions, sots, tt, rep):
    """What changed since the last published version. Each build saves a snapshot of every record's code
    in build/state/current.json; publishing copies it to published.json, the baseline for the next build."""
    state = STATE / 'current.json'
    last_path = state.with_name('published.json')
    now = {'bills': {k: b['sub'] for s in sessions for k, b in s['bills'].items()},
           'sots': {str(r[0]): r[4] for r in sots}, 'tt': {str(r[0]): r[8] for r in tt}}
    if last_path.exists():
        last = json.loads(last_path.read_text())
        for ds, label in (('bills', 'Bills'), ('sots', 'Speech sentences'), ('tt', 'Reports')):
            a, b = last.get(ds, {}), now[ds]
            new = [k for k in b if k not in a]
            gone = [k for k in a if k not in b]
            coded = [k for k in b if k in a and a[k] is None and b[k] is not None]
            recoded = [f'{k}: {a[k]} → {b[k]}' for k in b if k in a and a[k] is not None and b[k] is not None and a[k] != b[k]]
            uncoded = [k for k in b if k in a and a[k] is not None and b[k] is None]
            rep.changes[ds] = {'new': len(new), 'removed': len(gone), 'newly_coded': len(coded), 'recoded': len(recoded)}
            if new or coded or recoded:
                rep.note(label, f'Since the last published version: {len(new):,} new, {len(coded):,} newly coded, {len(recoded):,} recoded.', recoded[:12])
            if gone:
                (rep.warn if len(gone) > max(20, 0.02 * len(a)) else rep.note)(label, f'{len(gone):,} records on the published site are no longer in the files.', gone)
            if uncoded:
                rep.warn(label, f'{len(uncoded):,} records have lost the code they have on the published site.', uncoded)
    else:
        rep.note('Build', 'Nothing has been published yet, so there is nothing to compare with.')
    return state, now


def write_worksheet(ga, sessions, rep):
    s = next((s for s in sessions if s['ga'] == ga), None)
    if not s:
        rep.error('Worksheet', f'No session {ga} in config.toml.')
        return None
    if s.get('upcoming'):
        rep.note('Worksheet', f'The {ga}th General Assembly has no bills yet, so there is nothing to code. Make a worksheet once the scraper has run.')
        return None
    rows = [{'bill': b['bill'], 'introduced': b['intro'], 'short_description': b['short'], 'synopsis': b['synopsis'], 'sponsor': b['sponsors_raw'].split(',')[0],
             'subtopic': '', 'coder': '', 'notes': ''} for b in sorted(s['bills'].values(), key=lambda b: (b['intro'] or '', b['bill'])) if b['sub'] is None]
    path = DATA / 'inputs' / 'worksheets' / f'uncoded_{ga}_{dt.date.today():%Y-%m-%d}.xlsx'
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)
    rep.note('Worksheet', f'Wrote {len(rows):,} uncoded bills to {show(path)}. Fill in `subtopic`, then save it into {Path(next(x for x in CFG["bills"]["sessions"] if int(x["ga"]) == ga)["codes"])}/.')
    return path


def write_report(cfg, rep, manifest, path):
    esc = html.escape
    lv = {'error': ('Errors', 'Fix these before publishing.'), 'warning': ('Warnings', 'Worth a look; publishing is still allowed.'), 'note': ('Notes', '')}
    parts = []
    for level in ('error', 'warning', 'note'):
        items = [i for i in rep.items if i[0] == level]
        items.sort(key=lambda i: not i[2].startswith('Since the last published'))
        if not items:
            continue
        parts.append(f'<h2 class="{level}">{lv[level][0]} <span>{len(items)}</span></h2><p class="sub">{lv[level][1]}</p>')
        for _, ds, msg, ex in items:
            exs = ''.join(f'<li>{esc(str(e))}</li>' for e in ex)
            parts.append(f'<div class="item {level}"><b>{esc(ds)}</b> {esc(msg)}{f"<ul>{exs}</ul>" if exs else ""}</div>')
    stats = ''.join(f'<tr><td>{esc(k)}</td><td>{v.get("records", 0):,}</td><td>{v.get("coded", "") if v.get("coded") is None else format(v["coded"], ",")}</td><td>{"" if v.get("uncoded") is None else format(v["uncoded"], ",")}</td></tr>' for k, v in rep.stats.items())
    status = ('blocked', 'Not ready to publish', f'{len(rep.errors)} error{"s" if len(rep.errors) != 1 else ""} to fix.') if rep.errors else ('ok', 'Ready to publish', 'All checks passed.')
    path.write_text(f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Build report</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:24px 16px;color:#1b2330;background:#fff}}
.status{{padding:16px 18px;border-radius:8px;margin-bottom:20px}} .status.ok{{background:#e2f5ec}} .status.blocked{{background:#fde8e6}}
.status b{{font-size:20px;display:block}} h2{{font-size:17px;margin:24px 0 2px}} h2 span{{font-weight:400;color:#7b8494}}
.sub{{margin:0 0 8px;color:#4b5566;font-size:13.5px}} .item{{border-left:4px solid #cfd7e2;padding:6px 12px;margin:6px 0;background:#f7f9fb}}
.item.error{{border-color:#d03b3b}} .item.warning{{border-color:#eda100}} ul{{margin:4px 0 0;padding-left:18px;font:12.5px ui-monospace,Menlo,monospace;color:#4b5566}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{padding:6px 8px;border-bottom:1px solid #e4e9f0;text-align:right}} td:first-child,th:first-child{{text-align:left}}</style>
<div class="status {status[0]}"><b>{status[1]}</b>{status[2]} Built {esc(manifest["updated"] if manifest else dt.datetime.now().strftime("%Y-%m-%d %H:%M"))}.</div>
<h2>Records</h2><table><tr><th>Dataset</th><th>Records</th><th>Coded</th><th>Not yet coded</th></tr>{stats}</table>
{"".join(parts)}''', encoding='utf-8')


CFG = None


def main(argv=None):
    global CFG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default=str(ROOT / 'config.toml'))
    ap.add_argument('--worksheet', type=int, help='also write a coding worksheet of uncoded bills for this session (e.g. 104)')
    a = ap.parse_args(argv)
    try:
        CFG = cfg = tomllib.loads(Path(a.config).read_text(encoding='utf-8'))
    except Exception as e:  # noqa: BLE001
        print(f'Could not read {a.config}: {e}', file=sys.stderr)
        return 2
    rep = Report()
    print('Reading codebook…', flush=True)
    codebook, valid = load_codebook(cfg, rep)
    print('Reading bills…', flush=True)
    sessions, people, links = build_bills(cfg, valid, rep)
    print('Reading State of the State speeches…', flush=True)
    sots = build_sots(cfg, valid, rep)
    print('Reading think tank reports…', flush=True)
    tt = build_tt(cfg, valid, rep)
    governors = load_governors(cfg, sots, rep)
    home, home_media = build_home(cfg, rep)
    legislators, leg_photos = build_legislators(cfg, rep)
    profiles, logo_copies = build_profiles(cfg, tt, rep)
    if a.worksheet:
        write_worksheet(a.worksheet, sessions, rep)
    state, snapshot = compare_with_last(cfg, sessions, sots, tt, rep)
    print('Writing the site…', flush=True)
    out, manifest = write_site(cfg, codebook, sessions, people, links, sots, tt, rep, profiles, list(logo_copies) + home_media + leg_photos, governors, home, legislators)
    report = ROOT / cfg['build']['output'] / 'report.html'
    (ROOT / cfg['build']['output'] / 'status.json').write_text(json.dumps({'ok': not rep.errors, 'errors': len(rep.errors), 'built': manifest['updated']}))
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(snapshot))
    write_report(cfg, rep, manifest, report)
    for level, ds, msg, _ in rep.items:
        print(f'  [{level}] {ds}: {msg}')
    print(f'\n{"READY TO PUBLISH" if not rep.errors else "NOT READY: fix the errors above"}. Report: {report}')
    return 1 if rep.errors else 0


if __name__ == '__main__':
    sys.exit(main())
