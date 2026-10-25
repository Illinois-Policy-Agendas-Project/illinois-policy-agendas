# Legislators

`members.csv` and `photos/` are made by **Fetch legislators** in the control panel (`pipeline/fetch_legislators.py`),
which reads the House and Senate member lists on ilga.gov. The site uses them for each sponsor's party, district and
official photo, and to color the co-sponsorship network by party.

To fix a row by hand, edit `members.csv` and set its `source` column to `manual`; later fetches keep it.
Members who left before the current list was posted can be added the same way (chamber, district, name, party, url,
photo file name, `manual`). Photos: Illinois General Assembly.
