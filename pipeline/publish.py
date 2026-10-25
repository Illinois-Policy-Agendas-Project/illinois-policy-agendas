#!/usr/bin/env python3
"""Publish the built site (build/site) using the method set in config.toml [publish].

    python pipeline/publish.py            publish the last build (refuses if it had errors)
    python pipeline/publish.py --force    publish even though the last build had errors

For SFTP, the password is read from the IL_AGENDAS_SFTP_PASSWORD environment variable or asked for;
it is never written to disk. An SSH key works too, and then no password is needed.
"""
import argparse, csv, datetime as dt, getpass, hashlib, json, os, shutil, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('IL_AGENDAS_DATA') or ROOT).expanduser().resolve()
STATE = DATA / 'state'   # shared: what was last published


def files_with_hashes(site):
    out = {}
    for p in sorted(site.rglob('*')):
        if p.is_file():
            out[p.relative_to(site).as_posix()] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def to_folder(site, target, log):
    """Copy into a mapped drive or mounted web folder. Old data files are removed; nothing else on the target is touched."""
    target = Path(target).expanduser()
    if not target.exists():
        raise SystemExit(f'The publish folder does not exist: {target}\nCheck that the drive is connected, or fix `folder` in config.toml.')
    if (target / 'data').exists():
        shutil.rmtree(target / 'data')
    shutil.copytree(site, target, dirs_exist_ok=True)
    log(f'Copied the site into {target}')
    return str(target)


def to_zip(site, zip_dir, log):
    zip_dir = (ROOT / zip_dir) if not Path(zip_dir).is_absolute() else Path(zip_dir)
    zip_dir.mkdir(parents=True, exist_ok=True)
    name = zip_dir / f'illinois-policy-agendas-{dt.datetime.now():%Y-%m-%d-%H%M}'
    path = shutil.make_archive(str(name), 'zip', site)
    log(f'Wrote {path}. Send it to whoever uploads the site, or unzip it into the web folder.')
    return path


def to_sftp(site, cfg, log, password=None):
    try:
        import paramiko
    except ImportError:
        raise SystemExit('SFTP needs the paramiko package. Run "Update website" again so it can install it, or: pip install paramiko')
    host, user = cfg.get('host'), cfg.get('username')
    if not host or not user:
        raise SystemExit('Set `host` and `username` under [publish] in config.toml.')
    remote = cfg.get('remote_dir', 'public_html').rstrip('/')
    state = STATE / f'uploaded-{host}.json'
    before = json.loads(state.read_text()) if state.exists() else {}
    now = files_with_hashes(site)
    changed = [f for f, h in now.items() if before.get(f) != h]
    stale = [f for f in before if f not in now]
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    password = password or os.environ.get('IL_AGENDAS_SFTP_PASSWORD')
    try:
        client.connect(host, port=int(cfg.get('port', 22)), username=user, password=password, timeout=30)
    except paramiko.AuthenticationException:
        if password is not None or not sys.stdin.isatty():
            raise SystemExit('The server rejected the login. Check the username and password.')
        client.connect(host, port=int(cfg.get('port', 22)), username=user, password=getpass.getpass(f'Password for {user}@{host}: '), timeout=30)
    sftp = client.open_sftp()

    def mkdirs(path):
        cur = ''
        for part in path.split('/')[:-1]:
            cur = f'{cur}/{part}' if cur else part
            try:
                sftp.stat(cur)
            except FileNotFoundError:
                sftp.mkdir(cur)
    for i, f in enumerate(changed, 1):
        dest = f'{remote}/{f}'
        mkdirs(dest)
        sftp.put(str(site / f), dest + '.uploading')
        sftp.posix_rename(dest + '.uploading', dest)  # swap in place so visitors never see a half-written file
        log(f'Uploaded {f} ({i}/{len(changed)})')
    for f in stale:
        try:
            sftp.remove(f'{remote}/{f}')
            log(f'Removed {f}')
        except FileNotFoundError:
            pass
    sftp.close(); client.close()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(now))
    log(f'Uploaded {len(changed)} changed file(s) to {host}:{remote}; {len(now) - len(changed)} were already up to date.')
    return f'{host}:{remote}'


def to_github(site, cfg, built, log):
    """Copy the built site into docs/ of this repository (GitHub Pages serves main /docs), commit, and push.
    Content edits made on this computer (e.g. fetched logos) go up in the same commit.
    Signing in to GitHub is handled by git itself (Git Credential Manager, GitHub Desktop or `gh auth login`)."""
    import subprocess
    branch = cfg.get('github_branch', 'main')

    def git(*args, check=True):
        r = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        if check and r.returncode:
            raise SystemExit(f'git {" ".join(args)} failed:\n{(r.stderr or r.stdout).strip()}\n'
                             'If this is a sign-in problem, sign in to GitHub once (run "gh auth login", or sign in with GitHub Desktop) and publish again.')
        return r.stdout.strip()
    if shutil.which('git') is None:
        raise SystemExit('Publishing to GitHub needs git. Install it (https://git-scm.com/downloads), then try again.')
    if not (ROOT / '.git').exists():
        raise SystemExit(f'{ROOT} is not a copy of the GitHub repository. Start the site tools with the "Update website" launcher, which sets this up.')
    docs = ROOT / 'docs'
    if docs.exists():
        shutil.rmtree(docs)
    shutil.copytree(site, docs)
    (docs / '.nojekyll').write_text('')  # serve files as they are; no Jekyll processing
    git('add', '-A', 'docs', 'content')
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode == 0:
        log('GitHub already has this version of the site; nothing to push.')
        return 'github'
    git('-c', 'user.name=Illinois Policy Agendas', '-c', 'user.email=noreply@users.noreply.github.com', 'commit', '-q', '-m', f'Publish site (data built {built})')
    log('Bringing in any changes made elsewhere…')
    git('pull', '--rebase', '--autostash', 'origin', branch)
    log('Pushing to GitHub…')
    git('push', 'origin', f'HEAD:{branch}')
    log('Pushed. GitHub Pages usually shows the new version within a minute or two.')
    return 'github'


def main(argv=None, log=print, password=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default=str(ROOT / 'config.toml'))
    ap.add_argument('--force', action='store_true', help='publish even if the last build had errors')
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding='utf-8'))
    out = ROOT / cfg['build']['output']
    site, status_file = out / 'site', out / 'status.json'
    if not site.exists() or not status_file.exists():
        raise SystemExit('Nothing to publish yet. Run "Check data" first.')
    status = json.loads(status_file.read_text())
    if not status['ok'] and not a.force:
        raise SystemExit(f'The last build had {status["errors"]} error(s). Fix them and check again, or publish with --force.')
    pub = cfg['publish']
    method = pub.get('method', 'zip')
    log(f'Publishing the build from {status["built"]} by {method}…')
    if method == 'folder':
        where = to_folder(site, pub.get('folder', ''), log)
    elif method == 'sftp':
        where = to_sftp(site, pub, log, password)
    elif method == 'github':
        where = to_github(site, pub, status['built'], log)
    elif method == 'zip':
        where = to_zip(site, pub.get('zip_dir', 'build/releases'), log)
    else:
        raise SystemExit(f'Unknown publish method "{method}". Use github, folder, sftp or zip in config.toml.')
    # this build becomes the baseline for "what changed since the last published version"
    st = STATE
    st.mkdir(parents=True, exist_ok=True)
    if (st / 'current.json').exists():
        shutil.copyfile(st / 'current.json', st / 'published.json')
    logf = st / 'publish_log.csv'
    new = not logf.exists()
    with open(logf, 'a', newline='') as f:
        w = csv.writer(f)
        if new:
            w.writerow(['published_at', 'build', 'method', 'destination', 'had_errors'])
        w.writerow([dt.datetime.now().strftime('%Y-%m-%d %H:%M'), status['built'], method, where, not status['ok']])
    log('Published.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
