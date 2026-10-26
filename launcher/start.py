#!/usr/bin/env python3
"""Start the Illinois Policy Agendas Project (IPAP) website tools.

This file lives in Dropbox (IAP/Website), next to the project's data. Each time it runs it:
  1. gets the latest site code from GitHub into ~/illinois-policy-agendas (outside Dropbox),
  2. sets up Python packages there (first run only, or when requirements change),
  3. opens the control panel, pointed at the data in this Dropbox folder.
"""
import hashlib, os, shutil, subprocess, sys, venv
from pathlib import Path

REPO = 'https://github.com/Illinois-Policy-Agendas-Project/illinois-policy-agendas'
HERE = Path(__file__).resolve().parent                     # Dropbox IAP/Website: the data folder
CODE = Path(os.environ.get('IL_AGENDAS_CODE') or Path.home() / 'illinois-policy-agendas')

if sys.version_info < (3, 11):
    sys.exit(f'This needs Python 3.11 or newer; this computer has {sys.version.split()[0]}.\n'
             'Install the current Python from https://www.python.org/downloads/ and try again.')
if not shutil.which('git'):
    sys.exit('This needs git. Install it from https://git-scm.com/downloads (on a Mac you can also run\n'
             '"xcode-select --install"), then sign in to GitHub once with "gh auth login" or GitHub Desktop.')

if not (CODE / '.git').exists():
    print(f'First run on this computer: copying the site code from GitHub into {CODE}…', flush=True)
    subprocess.check_call(['git', 'clone', REPO, str(CODE)])
else:
    print('Getting the latest site code from GitHub…', flush=True)
    r = subprocess.run(['git', '-C', str(CODE), 'pull', '--rebase', '--autostash'], capture_output=True, text=True)
    if r.returncode:
        print('Could not update the code (working offline?). Using the copy already on this computer.\n' + r.stderr.strip(), flush=True)

ENV = Path.home() / '.il-agendas' / 'venv'
py = ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
req = CODE / 'requirements.txt'
stamp = ENV / 'requirements.sha1'
want = hashlib.sha1(req.read_bytes()).hexdigest()
if not py.exists() or not stamp.exists() or stamp.read_text() != want:
    print('Installing what the website builder needs (a few minutes, first time only)…', flush=True)
    venv.EnvBuilder(with_pip=True, clear=py.exists()).create(ENV)
    subprocess.check_call([str(py), '-m', 'pip', 'install', '--quiet', '--upgrade', 'pip'])
    subprocess.check_call([str(py), '-m', 'pip', 'install', '--quiet', '-r', str(req)])
    stamp.write_text(want)

os.chdir(CODE)
sys.exit(subprocess.call([str(py), str(CODE / 'pipeline' / 'app.py')], env={**os.environ, 'IL_AGENDAS_DATA': str(HERE)}))
