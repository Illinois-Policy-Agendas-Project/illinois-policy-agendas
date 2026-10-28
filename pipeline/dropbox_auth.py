#!/usr/bin/env python3
"""One-time setup for running the update on GitHub: get a Dropbox refresh token for the IPAP Dropbox app.

    python pipeline/dropbox_auth.py

It asks for the app key and app secret (from the app's page at https://www.dropbox.com/developers/apps), shows a link
to open while signed in to the Dropbox account that holds the IAP folder, and asks for the code Dropbox shows after you
allow access. It then prints the refresh token. Save the key, secret and token as GitHub secrets (see the README).
Nothing is written to disk.
"""
import getpass, json, urllib.error, urllib.parse, urllib.request

HINTS = {
    'invalid_grant': 'The code was already used or has expired (codes last a few minutes and work once). Open the link again for a new code.',
    'invalid_client': 'Dropbox did not accept the app key and secret together. Copy both again from the app\'s Settings tab (the secret: click Show).',
    'invalid_request': 'Something was missing or mistyped. Check the app key and the code.',
}

key = input('App key: ').strip()
secret = getpass.getpass('App secret (not shown as you type): ').strip()
link = 'https://www.dropbox.com/oauth2/authorize?' + urllib.parse.urlencode({'client_id': key, 'response_type': 'code', 'token_access_type': 'offline'})
while True:
    print('\nOpen this link, sign in to the Dropbox account that has the IAP folder, and click Allow:\n')
    print('  ' + link)
    code = input('\nPaste the code Dropbox shows (right away; it expires in a few minutes): ').strip()
    body = urllib.parse.urlencode({'code': code, 'grant_type': 'authorization_code', 'client_id': key, 'client_secret': secret}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request('https://api.dropboxapi.com/oauth2/token', data=body), timeout=60) as r:
            tok = json.loads(r.read())
        break
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            err = json.loads(raw)
            kind, why = err.get('error', ''), err.get('error_description', '')
        except ValueError:
            kind, why = '', raw
        print(f'\nDropbox said: {kind} {why}'.rstrip())
        print(HINTS.get(kind, 'Check the app key, the secret and the code, then try again.'))
        if kind == 'invalid_client':
            secret = getpass.getpass('App secret again (not shown): ').strip()
        if input('Try again? [Y/n] ').strip().lower().startswith('n'):
            raise SystemExit(1)
print('\nRefresh token (save it as the GitHub secret DROPBOX_REFRESH_TOKEN; it does not expire):\n')
print('  ' + tok['refresh_token'])
