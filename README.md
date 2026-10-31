# Illinois Policy Agendas Project (IPAP) website

The public website for the Illinois Policy Agendas Project (IPAP): substantive bills, State of the State speeches
and think tank reports in Illinois, coded by Comparative Agendas Project subtopic. GitHub Pages serves the site
from `docs/` at https://www.illinoisagendas.org (the domain is registered at Hover; its DNS points to GitHub, and
`docs/CNAME`, which publishing keeps, tells GitHub the domain).

The project keeps its parts in two places:

- **GitHub (this repository)** holds the site's code and text: the page design, the build pipeline, the home page
  and think tank profiles, logos and photos, and the published site in `docs/`.
- **Dropbox (`IAP/`)** holds the data: the scraped bill files, coders' files, speeches, reports, the subtopic
  index and governor metadata. Data files are never committed here. The website only publishes what the build
  derives from them.

## For coders

### The coding sheet (new bills and bills an amendment rewrote)

Bills that need a code collect in a Google Sheet, one tab per session ("104th GA"). The nightly update adds:

- every **new bill**, as soon as ILGA lists it;
- every bill an amendment **rewrote** (it replaced everything after the enacting clause with something other than
  the introduced bill again), with its earlier code under **Code before**. Leader placeholder bills that got real
  content say **Placeholder given content**.

For each row, read **What the bill does now** (the synopsis from the last rewrite on; **Status** opens it on
ilga.gov) and pick a **Subtopic** from the list: type a code or a few words to search it. If the earlier code still
fits, pick it again. Put your initials under **Coder**. Delete rows that don't need a code; they don't come back.
Sort and filter freely; the update finds rows by bill number.

Codes in the sheet go on the site at the next update, and override the bill file and coding files (Check law codes
still has the last word on laws). A code that isn't in the codebook blocks publishing until it's fixed. If a bill is
rewritten again after you code it, its row is updated: your code moves to **Code before** and Subtopic is cleared.

**Checking law codes in the sheet.** The **104th GA laws** tab lists every law, grouped by code, with the codebook
rule for its code. For each, pick a **Decision**: **Correct**, **Change code** (and pick a **New code**), or **Unsure**
(say why in **Notes**). Put your initials under **Reviewer**. New laws are added as they're signed. Decisions count
exactly like ones made in **Check law codes** (below): at the next update they're saved as
`inputs/qc/<session>/review_coding-sheet.csv`, a changed code overrides every other code, and if the sheet and the
Check law codes page disagree, the most recent decision wins.

### Coding worksheets (without the sheet)

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

**Publishing an update is three steps:**

1. **Update and check.** Gets every bill's latest status from the General Assembly (ftp.ilga.gov), looks for new
   think tank reports, then reads every file, checks every code and rebuilds the site on this computer. A few minutes;
   about 40 the first time on a computer. If a download fails (a site is down or blocks the request), the panel says
   so and the check still runs on what is there. **Check only** skips the downloads.
2. **Look it over.** **Open report** and **Preview site**.
   - **Errors** block publishing: a code that isn't in the codebook, coders who disagree, a missing or unreadable file, missing columns.
   - **Warnings** are worth a look but don't block anything: duplicate bills, a coded bill the scraper hasn't picked up yet, records that lost their code.
   - The report also says what changed since the last publish.
3. **Publish.** Only enabled when the check found no errors. It copies the site into `docs/`, commits and pushes.
   GitHub Pages shows the new version within a minute or two.

**Coding** shows what needs coders' attention after the last check: bills without a code (with **Make worksheet**),
new think tank reports waiting for a code, and how many laws have been reviewed in **Check law codes**.

**Occasionally** (folded away): **Fetch legislators** at the start of each session, and the two
downloads of Update and check on their own.

### Updating from GitHub instead of a lab computer

The same update also runs on GitHub's servers (`.github/workflows/update.yml`), so no lab computer is needed.

- **Every night** it updates bills from ILGA, looks for new think tank reports and checks the data. It saves the
  downloads back to Dropbox but **never publishes**.
- **To publish:** on GitHub, open the repository's **Actions** tab → **Update website** → **Run workflow**, tick
  **Publish the site if the check passes**, and click **Run workflow**. It publishes only if the check finds no errors.
  The same form can skip the downloads, or make a coding worksheet for a session (saved to `inputs/worksheets` in
  Dropbox).
- **Each run's page** shows a summary (errors, warnings, what needs coding); the full report is under **Artifacts**.
  GitHub emails you when a run fails.
- It reads the data from Dropbox and writes back only what it produced (bill status from ILGA, new think tank
  reports, worksheets, the publish log). A file someone edits in Dropbox during a run is never overwritten.
- It also keeps the **coding sheet** up to date: it reads the sheet's codes before the check and adds new and
  rewritten bills after it. A lab computer's **Update and check** uses the copy of the sheet's codes from the last
  GitHub run (`inputs/coding_sheet/` in Dropbox).
- **Check law codes**, **Preview site** and **Fetch legislators** still need the control panel on a computer.
- GitHub pauses scheduled runs in a repository with no commits for 60 days; publishing (or any commit) restarts them.

**Setting it up (once, about 15 minutes):**

1. **Make a Dropbox app.** At https://www.dropbox.com/developers/apps, signed in to the Dropbox account that has the
   IAP folder: **Create app** → **Scoped access** → **Full Dropbox** → name it (e.g. "IPAP website"). On its
   **Permissions** tab tick `files.metadata.read`, `files.content.read` and `files.content.write`, then **Submit**. Its
   **Settings** tab shows the **App key** and **App secret**.
2. **Get a refresh token.** On any computer with Python, in this repository, run `python pipeline/dropbox_auth.py`. It
   asks for the key and secret, gives you a link to allow access, and prints the token.
3. **Give them to GitHub.** In the repository: **Settings** → **Secrets and variables** → **Actions** → **New repository
   secret**, three times: `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`. If the IAP folder isn't at
   `/Academic/UIC/Research/IAP` in that Dropbox, also add a **variable** (same page, Variables tab) `DROPBOX_IAP_PATH`
   with its path.
4. **Connect the coding sheet** (optional, about 10 minutes).
   - In Google Cloud (https://console.cloud.google.com), make a project (e.g. "IPAP website"), then
     **APIs & Services** → **Library** → **Google Sheets API** → **Enable**.
   - **IAM & Admin** → **Service accounts** → **Create service account** (e.g. "ipap-coding-sheet"; no roles needed).
     Open it → **Keys** → **Add key** → **Create new key** → **JSON**. A .json file downloads.
   - Add a repository secret `GOOGLE_SERVICE_ACCOUNT` and paste the whole contents of that file. Then delete the file.
   - Share the coding sheet with the service account's email (`…@….iam.gserviceaccount.com`) as an **Editor**, and put
     the sheet's ID (the long part of its link between `/d/` and `/edit`) in `config.toml` under `[coding_sheet]`.
   The next run sets up the tabs and the subtopic list and adds the first bills.
5. **Try it.** Actions tab → **Update website** → **Run workflow** with Publish unticked. The first run takes about 45
   minutes (it downloads every bill from ILGA); later runs a few minutes.

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
  `reviewed = true` once checked). Logos are files in `content/thinktanks/logos/` named in each block's `logo`; add or
  replace them by hand, then commit. Use logos with each organization's permission.
- **Legislators' party, district and photos:** click **Fetch legislators** in the control panel (or run
  `python pipeline/fetch_legislators.py`). It reads the House and Senate member lists on ilga.gov and saves
  `content/legislators/members.csv` and `content/legislators/photos/`. Check them with **Review legislators**, then publish.
  Run it at the start of each General Assembly. Sponsor pages then show party, district and photo, and the
  co-sponsorship network can be colored by party.
- **New think tank reports:** click **Update think tank reports** in the control panel (or run
  `python pipeline/fetch_reports.py`). For each organization with a source under `[[thinktanks.sources]]` in
  `config.toml` it reads the organization's listing, skips reports already on file, and adds new ones (title, date,
  summary, link and PDF) to `IAP/Website/inputs/thinktanks/new_reports.xlsx` with an empty `subtopic`. Coders fill in
  `subtopic` there and delete rows that aren't reports; each report goes on the site once it is coded. Deleted rows are
  not offered again (`found.json` remembers them). It only looks back to a few weeks before each organization's latest
  report on file. Sites that block automated requests (CTBA and ILEPI did from a cloud server) and organizations
  without a source are collected by hand into the main reports file as before.
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

**Visitor counts.** The site counts page views with GoatCounter (no cookies, no personal data). Sign up at
https://www.goatcounter.com (free for non-commercial sites), then put the code you chose (e.g. `ipap` for
ipap.goatcounter.com) under `[analytics] goatcounter` in `config.toml`, and publish. Every view counts as its own
page (`/speeches`, `/bill-104-HB35`); local previews are never counted. The stats are at https://<code>.goatcounter.com.

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
| `pipeline/fetch_legislators.py` | Collects legislators' party, district and photos from ilga.gov |
| `launcher/` | The files that go in Dropbox `IAP/Website/` |
| `site/index.html` | The website itself |
| `content/` | Home page text, think tank profiles, logos and photos |
| `docs/` | The published site that GitHub Pages serves. Generated; don't edit by hand |
| `build/` | Local build output (not committed) |
