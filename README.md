# Illinois Policy Agendas Project (IPAP) website

The public website for the Illinois Policy Agendas Project (IPAP): substantive bills, State of the State speeches
and think tank reports in Illinois, coded by Comparative Agendas Project subtopic. GitHub Pages serves the site
from `docs/`.

The project keeps its parts in two places:

- **GitHub (this repository)** holds the site's code and text: the page design, the build pipeline, the home page
  and think tank profiles, logos and photos, and the published site in `docs/`.
- **Dropbox (`IAP/`)** holds the data: the scraped bill files, coders' files, speeches, reports, the subtopic
  index and governor metadata. Data files are never committed here. The website only publishes what the build
  derives from them.

## For coders

1. Open **Update website** (see below), pick the session under **Coding worksheet**, and click **Make worksheet**.
   You get `IAP/Website/inputs/worksheets/uncoded_104_<date>.xlsx`, listing every bill that has no code yet.
2. Fill in the `subtopic` column with a code from the Illinois subtopic index (`IAP/illinois_subtopic_index.csv`;
   `-555` for no policy content). Leave a row blank if you're unsure. `coder` and `notes` are for you.
3. Save your file into the session's codes folder, e.g. `IAP/Website/inputs/codes/104/`. Give it any name. Each
   coder can keep their own file, and every `.xlsx` or `.csv` in the folder is read.

### Checking the codes of enacted laws

Open **Update website**, run **Check data**, then click **Check law codes**. Enter your name and work through every
bill that became law:

- **One at a time:** read the synopsis (every version, with the amendment that rewrote the bill marked) and the
  codebook rule for the current code, then press **Enter** if the code is right, **C** to change it (type a code or
  words and pick with the arrow keys), or **U** to flag it as unsure with a note. **←/→** move back and forward.
- **By subtopic:** all laws under one code in a list. Change the ones that don't fit, then mark the rest correct with
  one click. Usually the fastest way through.

Decisions save as you go, one file per reviewer in `IAP/Website/inputs/qc/<session>/`, so several people can review
at once. Changed codes override every other code at the next **Check data**; the report lists laws flagged as unsure
and any laws reviewers coded differently. Then **Publish**.

A code in a coding file overrides any code already in the scraped bill file. If two files give the same bill
different codes, the check reports it and the bill stays "Not yet coded" until you agree.

## For whoever updates the site

Double-click **Update website.command** (Mac) or **Update website.bat** (Windows) in `IAP/Website/`. It gets the
latest code from GitHub into `~/illinois-policy-agendas` and opens a control panel in your browser.

1. **Check data.** Reads all the files, checks every code, and builds the site on this computer (about a minute).
2. **Open report / Preview site.**
   - **Errors** block publishing: a code that isn't in the codebook, coders who disagree, a missing or unreadable file, missing columns.
   - **Warnings** are worth a look but don't block anything: duplicate bills, a coded bill the scraper hasn't picked up yet, records that lost their code.
   - The report also says what changed since the last publish.
3. **Publish.** It's only enabled when the check found no errors. It copies the site into `docs/`, commits and
   pushes. GitHub Pages shows the new version within a minute or two.

### Setting up a lab computer (once)

1. Install Python 3.11 or newer from https://www.python.org/downloads/.
2. Install git from https://git-scm.com/downloads (on a Mac, `xcode-select --install` also works).
3. Sign in to GitHub with an account that belongs to the Illinois-Policy-Agendas-Project organization: run
   `gh auth login` (GitHub CLI, https://cli.github.com) or sign in once in GitHub Desktop.
4. Double-click **Update website**. The first run takes a few minutes to install what it needs into `~/.il-agendas`.
   On a Mac, if double-clicking does nothing, open Terminal in `IAP/Website` and run `chmod +x "Update website.command"`.

The Dropbox folder `IAP/Website/` holds only `start.py`, the two **Update website** launchers (copies of
`launcher/` here), `inputs/` and `state/`. `state/` records what was last published, so "what changed" works on
every lab computer.

## Changing the site's design or text

Edit the files here, either directly on GitHub, on a computer with a clone, or by asking Claude Code to make the
change in this repository. Then refresh the published copy without touching the data:

    python pipeline/refresh_docs.py

That copies `site/index.html` into `docs/` and rebuilds the home page and think tank profiles from `content/`.
Commit and push, and the site updates. Lab computers pick up the change the next time someone opens **Update website**.

- **Home page:** `content/home/home.toml` (overview, methods note, team bios, sponsor and codebook link). Logos go in
  `content/home/logos/`, photos in `content/home/photos/`. Counts like {bills} in the text fill in automatically.
- **Think tank biographies and logos:** `content/thinktanks/profiles.toml` (one block per organization; set
  `reviewed = true` once checked). **Fetch logos** in the control panel (or `python pipeline/fetch_logos.py`) saves each
  organization's logo from its website into `content/thinktanks/logos/`. Check them with **Review logos**, then commit.
  Use logos with each organization's permission.
- **Legislators' party, district and photos:** click **Fetch legislators** in the control panel (or run
  `python pipeline/fetch_legislators.py`). It reads the House and Senate member lists on ilga.gov and saves
  `content/legislators/members.csv` and `content/legislators/photos/`. Check them with **Review legislators**, then publish.
  Run it at the start of each General Assembly. Sponsor pages then show party, district and photo, and the
  co-sponsorship network can be colored by party.
- **Keeping bills current (and the day-by-day replay):** click **Update bills from ILGA** in the control panel (or run
  `python pipeline/fetch_actions.py`) before **Check data**. It downloads every House and Senate bill's Bill Status file
  from the General Assembly's public file repository (ftp.ilga.gov, which ILGA asks automated tools to use instead of
  ilga.gov) into `IAP/Website/inputs/actions/<session>/`: every dated action (`bill_actions.csv`) and each bill's short
  description, sponsors and synopsis (`bill_status.csv`). The build then brings the scraped bill file up to date with
  them: newer actions, stages and Public Acts, current sponsors and synopses, and bills introduced since the scrape
  (listed in the report so they can be coded). The scraped file itself is not changed, and coders' codes still apply.
  The first run takes about 40 minutes; later runs download only bills that changed. Without these files the site
  builds from the scraped file alone and keeps the replay as last published.
- **Design and features:** `site/index.html`.

## Common data changes (edit `config.toml`)

Paths under `[codebook]`, `[governors]`, `[sots]`, `[thinktanks] file` and `[[bills.sessions]]` are relative to the
Dropbox folder `IAP/Website/`; `profiles` and `home` are relative to this repository.

- **New scrape of the current session:** overwrite the file named in `details` (same path, same name).
  New bills appear on the site as "Not yet coded", and existing codes are kept.
- **The 105th General Assembly (2027-2028):** already set up as a placeholder (`status = "upcoming"`). The site lists
  it as "not started" until the scraper saves `Bills/Live data/igbills_2027_2028_complete.xlsx`; the next Check picks
  it up. Put its coding files in `inputs/codes/105/`, and delete the `status` line once it's live.
- **A later or earlier General Assembly:** copy a session block, change `ga`, `years`, `starts` and `details`, and
  create `inputs/codes/<ga>/`.
- **The scraper renamed a column:** update `[bills.columns]`.

## What's where

| Path | What it is |
|---|---|
| `config.toml` | Where the data is and how to publish |
| `pipeline/build.py` | Reads, checks and builds into `build/site`. `IL_AGENDAS_DATA=<Dropbox>/IAP/Website python pipeline/build.py` runs it without the control panel |
| `pipeline/publish.py` | Publishes the last good build into `docs/` and pushes |
| `pipeline/refresh_docs.py` | Updates `docs/` after design or content changes, without the data |
| `pipeline/app.py` | The control panel |
| `pipeline/fetch_logos.py` | Collects think tank logos from their websites |
| `pipeline/fetch_legislators.py` | Collects legislators' party, district and photos from ilga.gov |
| `launcher/` | The files that go in Dropbox `IAP/Website/` |
| `site/index.html` | The website itself |
| `content/` | Home page text, think tank profiles, logos and photos |
| `docs/` | The published site that GitHub Pages serves. Generated; don't edit by hand |
| `build/` | Local build output (not committed) |
