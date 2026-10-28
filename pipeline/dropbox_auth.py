#!/usr/bin/env python3
"""One-time setup for running the update on GitHub: get a Dropbox refresh token for the IPAP Dropbox app.

    python pipeline/dropbox_auth.py

It asks for the app key and app secret (from the app's page at https://www.dropbox.com/developers/apps), shows a link
to open while signed in to the Dropbox account that holds the IAP folder, and asks for the code Dropbox shows after you
allow access. It then prints the refresh token. Save the key, secret and token as GitHub secrets (see the README).
Nothing is written to disk.
"""
import getpass, json, urllib.parse, urllib.request

key = input('App key: ').strip()
secret = getpass.getpass('App secret (not shown): ').strip()
print('\nOpen this link, sign in to the Dropbox account that has the IAP folder, and click Allow:\n')
print('  https://www.dropbox.com/oauth2/authorize?' + urllib.parse.urlencode({'client_id': key, 'response_type': 'code', 'token_access_type': 'offline'}))
code = input('\nPaste the code Dropbox shows: ').strip()
body = urllib.parse.urlencode({'code': code, 'grant_type': 'authorization_code', 'client_id': key, 'client_secret': secret}).encode()
with urllib.request.urlopen(urllib.request.Request('https://api.dropboxapi.com/oauth2/token', data=body), timeout=60) as r:
    tok = json.loads(r.read())
print('\nRefresh token (save it as the GitHub secret DROPBOX_REFRESH_TOKEN; it does not expire):\n')
print('  ' + tok['refresh_token'])
