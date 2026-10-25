# Memo: scrape Illinois legislators (party, district, headshot) for the website

**For:** a Claude Code session running locally in a clone of
`Illinois-Policy-Agendas-Project/illinois-policy-agendas`.

## Goal
Collect every current member of the Illinois House (118) and Senate (59), with:
- name
- chamber
- district
- party
- member page URL
- official headshot

Save the results where the website already expects them, then publish.

## Sources
- https://ilga.gov/House/Members and https://ilga.gov/Senate/Members (lists)
- Each member's own page on ilga.gov (photo; party and district if the list lacks them)

Fetch politely, with about 0.3 seconds between requests. Credit photos as "Photos: Illinois General Assembly".

## Output contract (the site and build depend on this exactly)
- `content/legislators/members.csv`, UTF-8, with header `chamber,district,name,party,url,photo,source`:
  - `chamber`: `House` or `Senate`
  - `district`: a number
  - `party`: `Democratic`, `Republican` or `Independent`
  - `name`: written "First M. Last", with any nickname in double quotes as ILGA writes it, e.g. `Emanuel "Chris" Welch`.
    Convert "Last, First" to this form.
  - `url`: the member's ilga.gov page
  - `photo`: the file name only
  - `source`: `ilga.gov`
- `content/legislators/photos/<chamber>-<slug>.jpg`:
  - `<chamber>` is `house` or `senate`, and `<slug>` is the lowercase ASCII name with runs of other characters
    turned into `-`. Example: `house-emanuel-chris-welch.jpg`.
  - JPEG, about 300 px wide, quality 85.
- Keep any existing row whose `source` is `manual`; those are hand edits.

## How
- **Preferred:** fix `pipeline/fetch_legislators.py` so it parses the real pages. It was written without access to
  ilga.gov and only tested against a mock. It already writes the files above and a review page,
  `build/legislators.html`. Downloads go through `pipeline/webget.py`.
- **If Python can't verify ilga.gov's certificate:** on the lab Mac this failed with "unable to get local issuer
  certificate", probably because the network inspects secure traffic.
  - Use Playwright with the system browser, or `curl`, to get the pages.
  - Or run from a different network.
  - Don't turn off certificate checking.
- Run `pip install -r requirements.txt` first (Pillow is needed for resizing).

## Check
1. Counts are about 118 House and 59 Senate. Every row has a party, a district and a photo that is a real headshot,
   not a logo or a placeholder. Open `build/legislators.html` to look them over.
2. Run `python3 pipeline/refresh_docs.py`. This publishes `docs/data/legislators.json` and photos into `docs/`
   without needing the project's Dropbox data.
3. Serve `docs/` locally (`python3 -m http.server` in `docs/`) and check:
   - `#sponsor-sen-don-harmon` shows a photo, party and district;
   - the co-sponsorship network on `#sponsors` is colored by party.
4. In the browser console, run `SPON.filter(p=>!p.party).map(p=>p.title)` to list sponsors who didn't match.
   - Most should be members who left mid-session. Add them by hand with `source` set to `manual` if their pages
     can be found.
   - If not, fix the name format.

## Finish
Commit `content/legislators/`, `docs/` and any changes to `pipeline/`, then push to `main`. GitHub Pages updates in
a minute or two.

Report the counts, how many sponsors matched, and any problems.
