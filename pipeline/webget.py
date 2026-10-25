"""Downloading web pages for the collectors (fetch_legislators.py, fetch_logos.py), with two certificate fixes.

1. Python from python.org on a Mac ships without a list of trusted certificates. If the system's list fails,
   certifi's list is tried.
2. Some sites, ilga.gov among them, send their certificate without the "intermediate" certificate that links it to a
   trusted root. Browsers fetch the missing piece from the address written inside the site's certificate (its
   "Authority Information Access" field); Python doesn't. When verification still fails, this does the same: it reads
   that address, downloads the intermediate (a public file, checked against the trusted roots like any other), adds
   it to the trusted list for this run, and tries again. Nothing is ever accepted without a full, verified chain.
"""
import socket, ssl, urllib.error, urllib.request
from urllib.parse import urlparse

EXTRA_CAFILE = None   # tests can point this at their own root certificate
_FIXED = {}           # host -> ssl context that verified it


def _base():
    """Contexts to try, in order: the system's certificates, then certifi's."""
    out = [ssl.create_default_context(cafile=EXTRA_CAFILE)] if EXTRA_CAFILE else [ssl.create_default_context()]
    try:
        import certifi
        out.append(ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        pass
    return out


def _ca_issuers_url(der):
    """The "CA Issuers" address in a certificate's Authority Information Access field (OID 1.3.6.1.5.5.7.48.2)."""
    oid = bytes.fromhex('06082b06010505073002')
    i = der.find(oid)
    if i < 0:
        return None
    j = i + len(oid)
    if j + 2 > len(der) or der[j] != 0x86:   # [6] uniformResourceIdentifier
        return None
    n, j = der[j + 1], j + 2
    if n & 0x80:                              # long-form length
        k, n = n & 0x7f, 0
        for b in der[j:j + k]:
            n = n * 256 + b
        j += k
    return der[j:j + n].decode('ascii', 'replace')


def _peer_der(host, port=443, timeout=20):
    """The certificate a site presents, read without checking it (only to find where its issuer lives)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock, ctx.wrap_socket(sock, server_hostname=host) as s:
        return s.getpeercert(binary_form=True)


def _as_pem(data):
    if data.lstrip().startswith(b'-----BEGIN CERTIFICATE-----'):
        return data.decode('ascii', 'replace')
    return ssl.DER_cert_to_PEM_cert(data)


def _with_intermediates(host, port, timeout):
    """Contexts that also trust the site's missing intermediate certificates (up to three levels)."""
    pems, der = [], _peer_der(host, port, timeout)
    for _ in range(3):
        url = _ca_issuers_url(der)
        if not url:
            break
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=timeout) as r:
            data = r.read()
        pem = _as_pem(data)
        if pem in pems:
            break
        pems.append(pem)
        der = ssl.PEM_cert_to_DER_cert(pem)
    if not pems:
        return []
    out = []
    for ctx in _base():
        # the intermediates only help build the chain: it must still end at a trusted root. Python 3.13+ turns on
        # "partial chain" checking by default, which would trust an added intermediate by itself, so switch it off.
        ctx.verify_flags &= ~getattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN', 0)
        ctx.load_verify_locations(cadata=''.join(pems))
        out.append(ctx)
    return out


def get(url, ua, timeout=25):
    """(bytes, content type, final url). Raises urllib.error.URLError with advice if no certificate fix works."""
    req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept': '*/*'})
    u = urlparse(url)
    host, port = u.hostname, u.port or 443
    tried, err = 0, None
    for stage in ('known', 'base', 'aia'):
        if stage == 'known':
            ctxs = [_FIXED[host]] if u.scheme == 'https' and host in _FIXED else []
        elif stage == 'base':
            ctxs = _base() if u.scheme == 'https' else [None]
        else:
            if u.scheme != 'https' or err is None:
                break
            try:
                ctxs = _with_intermediates(host, port, timeout)
            except Exception:  # noqa: BLE001 - fall through to the error below
                ctxs = []
        for ctx in ctxs:
            tried += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    if ctx is not None and u.scheme == 'https':
                        _FIXED[host] = ctx
                    return r.read(), r.headers.get('Content-Type', '').split(';')[0].strip().lower(), r.geturl()
            except urllib.error.URLError as e:
                if 'CERTIFICATE_VERIFY_FAILED' not in str(e):
                    raise
                err = e
    raise urllib.error.URLError(f'{err.reason if err else "could not connect"}. The site\'s certificate could not be verified even after '
                                'fetching its missing intermediate certificate. On a Mac, run "Install Certificates.command" in the Python '
                                'folder under Applications, then try again.')
