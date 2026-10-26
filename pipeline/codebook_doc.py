"""Read the project's Word codebook (content/home/codebook.docx) into data for the site's Codebook page.

The document's layout:
  front matter (title, "Updated …", contact, citation), then Heading 2 sections:
  "General Introduction" (paragraphs and a bulleted list of datasets), "General Coding Guidelines" (numbered rules),
  "List of Major and Subtopic Codes" with a Heading 3 per major topic ("1. State Macroeconomy"), under which each
  subtopic is "100: Economics (General)" followed by "Rule: …", optional "See also: …" and "Special Note: …" lines,
  and a closing "List of Major Topic Codes" (skipped; it repeats the headings).
Only the Python standard library is used.
"""
import html, re, zipfile

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def paragraphs(path):
    """(style, text, is_list_item) for every non-empty paragraph, in order."""
    x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8')
    out = []
    for p in re.findall(r'<w:p[ >].*?</w:p>', x, re.S):
        st = re.search(r'<w:pStyle w:val="([^"]+)"', p)
        text = html.unescape(''.join(re.findall(r'<w:t(?: [^>]*)?>([^<]*)</w:t>', p)))
        text = re.sub(r'\s+', ' ', text).strip()
        if text:
            out.append((st.group(1) if st else '', text, '<w:numPr>' in p))
    return out


def parse(path):
    doc = {'title': '', 'subtitle': '', 'updated': '', 'contact': '', 'citation': '', 'intro': [], 'datasets': [], 'guidelines': [], 'majors': []}
    section, major, sub = 'front', None, None
    paras = paragraphs(path)
    for i, (style, text, listed) in enumerate(paras):
        if style.startswith('Heading2') or style == 'Heading 2':
            t = text.lower()
            section = 'intro' if 'introduction' in t else 'guidelines' if 'guideline' in t else 'codes' if 'subtopic' in t else 'skip'
            major = sub = None
            continue
        if section == 'front':
            if not doc['title']:
                doc['title'] = text
            elif not doc['subtitle'] and not text.lower().startswith(('updated', 'contact')):
                doc['subtitle'] = text
            elif text.lower().startswith('updated'):
                doc['updated'] = text
            elif text.lower().startswith('contact:'):
                doc['contact'] = text.split(':', 1)[1].strip()
            elif text.lower().rstrip(':') == 'citation':
                continue
            elif paras[i - 1][1].lower().rstrip(':') == 'citation' or (doc['citation'] == '' and re.search(r'\b(19|20)\d\d\.', text)):
                doc['citation'] = text
            continue
        if section == 'intro':
            (doc['datasets'] if style.startswith('ListParagraph') or listed else doc['intro']).append(text)
        elif section == 'guidelines':
            doc['guidelines'].append(re.sub(r'^\d+\.\s*', '', text))
        elif section == 'codes':
            m = re.match(r'^(\d{1,2})\.\s*(.+)$', text)
            if style.startswith('Heading3') or style == 'Heading 3':
                major = {'code': int(m.group(1)) if m else None, 'name': m.group(2) if m else text, 'notes': [], 'subs': []}
                doc['majors'].append(major)
                sub = None
                continue
            if major is None:
                continue
            m = re.match(r'^(\d{3,4}):\s*(.+)$', text)
            if m:
                sub = {'code': int(m.group(1)), 'name': m.group(2).strip(), 'rule': '', 'see': '', 'notes': []}
                major['subs'].append(sub)
            elif text.lower().startswith('rule:') and sub:
                sub['rule'] = text.split(':', 1)[1].strip()
            elif text.lower().startswith('see also:') and sub:
                sub['see'] = text.split(':', 1)[1].strip()
            elif text.lower().startswith('special note:'):
                (sub['notes'] if sub else major['notes']).append(text.split(':', 1)[1].strip())
            elif sub:   # a further paragraph of the rule
                sub['rule'] = (sub['rule'] + ' ' + text).strip()
            else:
                major['notes'].append(text)
    return doc


if __name__ == '__main__':
    import json, sys
    d = parse(sys.argv[1])
    print(json.dumps({k: (v if k != 'majors' else f'{len(v)} majors, {sum(len(m["subs"]) for m in v)} subtopics') for k, v in d.items()}, indent=1, ensure_ascii=False)[:3000])
