#!/usr/bin/env python3
"""One-time setup for running the update on GitHub: get a Dropbox refresh token for the IPAP Dropbox app.

    python pipeline/dropbox_auth.py

It asks for the app key and app secret (from the app's page at https://www.dropbox.com/developers/apps), shows a link
to open while signed in to the Dropbox account that holds the IAP folder, and asks for the code Dropbox shows after you
allow access. It then prints the refresh token. Save the key, secret and token as GitHub secrets (see the README).
Nothing is written to disk.
"""
import getpass, json, shutil, subprocess, urllib.error, urllib.parse, urllib.request

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
refresh = tok['refresh_token']

# make sure it works: sign in with it the way GitHub will, and look for the IAP folder
iap = input('\nWhere is the IAP folder in this Dropbox? [/Academic/UIC/Research/IAP] ').strip() or '/Academic/UIC/Research/IAP'
body = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': refresh, 'client_id': key, 'client_secret': secret}).encode()
with urllib.request.urlopen(urllib.request.Request('https://api.dropboxapi.com/oauth2/token', data=body), timeout=60) as r:
    access = json.loads(r.read())['access_token']
req = urllib.request.Request('https://api.dropboxapi.com/2/files/get_metadata', data=json.dumps({'path': iap}).encode(),
                             headers={'Authorization': f'Bearer {access}', 'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=60):
        print(f'Checked: the token works and can see {iap}.')
except urllib.error.HTTPError as e:
    print(f'The token works, but Dropbox cannot find {iap} in this account ({e.read().decode()[:120]}). '
          'Sign in to the account that has the IAP folder, or set DROPBOX_IAP_PATH on GitHub to where it is.')

# save the three secrets straight to GitHub when the GitHub command-line tool is installed and signed in
repo = 'Illinois-Policy-Agendas-Project/illinois-policy-agendas'
if shutil.which('gh') and input(f'\nSave the key, secret and token as secrets of {repo} on GitHub now? [Y/n] ').strip().lower()[:1] != 'n':
    ok = True
    for name, value in (('DROPBOX_APP_KEY', key), ('DROPBOX_APP_SECRET', secret), ('DROPBOX_REFRESH_TOKEN', refresh)):
        r = subprocess.run(['gh', 'secret', 'set', name, '--repo', repo, '--body', value], capture_output=True, text=True)
        print(f'  {name}: ' + ('saved' if r.returncode == 0 else 'not saved: ' + (r.stderr or r.stdout).strip()))
        ok &= r.returncode == 0
    if ok:
        print('\nDone. Run the "Update website" workflow on GitHub to try it.')
        raise SystemExit
print(f'\nSave these three as GitHub secrets (Settings → Secrets and variables → Actions):')
print(f'  DROPBOX_APP_KEY        the app key you typed')
print(f'  DROPBOX_APP_SECRET     the app secret you typed')
print(f'  DROPBOX_REFRESH_TOKEN  this refresh token ({len(refresh)} characters; the one-time code was {len(code)}):\n')
print(refresh)
