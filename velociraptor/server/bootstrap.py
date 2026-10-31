#!/usr/bin/env python3
'''
Set up a Velociraptor server on an Ubuntu VM.

Runs on the VM as root, started by the Azure Custom Script Extension that
easy_install.py configures. Uses the standard library only, so it needs
nothing beyond the python3 that ships with Ubuntu.

Parameters are JSON, passed base64-encoded in CYCAS_BOOTSTRAP_PARAMS or read
from a file with --params. Required: dns, admin_user, admin_password,
gui_allowed_cidrs, velociraptor_url. See DEFAULTS for the optional ones.

The files it uses (hunts.vql, Server.Utils.BackupAzure.yaml and
Custom.Windows.Triage.Targets.AzureUpload.yaml)
are read from files_dir, by default the directory this script is in. On the VM the extension downloads them all
there; in the repo the artifacts live in velociraptor/definitions/.

Every step can be re-run: the server keys, the API client and the hunts are
created once and kept, the rest is rebuilt. To run part of it by hand:

    sudo python3 bootstrap.py --params params.json --only client-packages,upload

The last line of output is CYCAS_BOOTSTRAP_RESULT <json> on success, or
CYCAS_BOOTSTRAP_FAILED step=<name>: <reason>. The full log, including the
output of every command, is in <work_dir>/bootstrap.log.
'''

import argparse
import base64
import grp
import gzip
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

DEFAULTS = {
    'expected_clients':    100,
    'package_prefix':      'Velociraptor',
    'api_name':            'cycas_api',
    'msi_amd64_url':       '',   # empty: the MSI from the Velociraptor release
    'msi_x86_url':         '',
    'hunts_url':           '',   # empty: hunts.vql from files_dir
    'writeback_windows':   '',   # empty: keep the Velociraptor default
    'triage_targets_url':  'https://triage.velocidex.com/artifacts/Windows.Triage.Targets.zip',
    'upload_sas_url':      '',   # container SAS with write access; empty: no upload
    'upload_prefix':       'velociraptor-server',
    'backup_sas_url':      '',   # container SAS for Server.Utils.BackupAzure; empty: not set up
    'backup_zip_password': '',
    'work_dir':            '/opt/cycas-velociraptor',
    'files_dir':           str(Path(__file__).resolve().parent),
    'service_user':        'velociraptor',
    'installed_config':    '/etc/velociraptor/server.config.yaml',
    'datastore':           '/opt/velociraptor',
    'api_config':          '/opt/velociraptor/api.config.yaml',
    'api_address':         '127.0.0.1:8001',
    'tls_check_address':   '127.0.0.1:443',   # connected to with the DNS name as SNI
    'tls_check_timeout':   300,
}
REQUIRED = ('dns', 'admin_user', 'admin_password', 'gui_allowed_cidrs', 'velociraptor_url')

BACKUP_ARTIFACT = 'Server.Utils.BackupAzure'
TRIAGE_UPLOAD_ARTIFACT = 'Custom.Windows.Triage.Targets.AzureUpload'


class StepFailed(Exception):
    pass


def redact_url(url):
    '''Drop the query string, which holds the SAS token.'''
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(query='***' if parts.query else ''))


def parse_json_arrays(text):
    '''velociraptor query prints one JSON array per statement, back to back.'''
    decoder, rows, pos = json.JSONDecoder(), [], 0
    text = text.strip()
    while pos < len(text):
        value, pos = decoder.raw_decode(text, pos)
        rows.extend(value)
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return rows


class Bootstrap:
    def __init__(self, params):
        self.p     = params
        self.work  = Path(params['work_dir'])
        self.files = Path(params['files_dir'])

        # Releases from 0.77 ship the Linux binaries gzipped.
        match = re.search(r'velociraptor-v([\d.]+)-linux-amd64(\.gz)?$', params['velociraptor_url'])
        if not match:
            raise StepFailed('velociraptor_url must point to a velociraptor-v<version>-linux-amd64[.gz] release binary.')
        self.version = match.group(1)
        self.musl_url = params['velociraptor_url'].replace('-linux-amd64', '-linux-amd64-musl')

        self.bin           = self.work / 'bin' / f'velociraptor-v{self.version}'
        self.bin_musl      = self.work / 'bin' / f'velociraptor-v{self.version}-musl'
        self.config        = self.work / 'config' / 'server.config.yaml'
        self.client_config = self.work / 'config' / 'client.config.yaml'
        self.artifacts     = self.work / 'artifacts'
        self.packages      = self.work / 'packages'
        self.state         = self.work / 'state'
        self.log_file      = self.work / 'bootstrap.log'
        self.result        = {'gui_url': f'https://{params["dns"]}/', 'version': self.version}

        # The service user runs the API queries, so it must be able to reach
        # the binaries and the artifact files in here.
        self.work.mkdir(parents=True, exist_ok=True)
        self.work.chmod(0o755)
        for d in (self.bin.parent, self.config.parent, self.artifacts, self.packages, self.state):
            d.mkdir(exist_ok=True)
        self.config.parent.chmod(0o700)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def log(self, msg, console=True):
        line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}'
        if console:
            print(line, flush=True)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def run(self, cmd, user=None, shown=None, check=True):
        '''Run a command. shown is the redacted form written to the log.'''
        cmd   = [str(c) for c in cmd]
        shown = [str(c) for c in shown] if shown else cmd
        if user:
            cmd   = ['runuser', '-u', user, '--'] + cmd
            shown = ['runuser', '-u', user, '--'] + shown
        self.log('$ ' + shlex.join(shown))
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in result.stderr.splitlines():
            self.log('  ' + line, console=False)
        if check and result.returncode != 0:
            lines = result.stderr.strip().splitlines() or ['no output']
            raise StepFailed(f'{Path(cmd[0]).name} exited with {result.returncode}: {lines[-1]}')
        return result

    def vql(self, query, env=None, secret=()):
        '''Run VQL on the server through the API. Values are passed with
        --env, so they never need quoting inside the query.'''
        cmd   = [self.bin, '--api_config', self.p['api_config'], 'query', '--format', 'json']
        shown = list(cmd)
        for key, value in (env or {}).items():
            cmd   += ['--env', f'{key}={value}']
            shown += ['--env', f'{key}={"***" if key in secret else value}']
        cmd   += ['--', query]
        shown += ['--', ' '.join(query.split())]
        result = self.run(cmd, user=self.p['service_user'], shown=shown)
        # Failing VQL functions return null and only log an error.
        errors = [l for l in result.stderr.splitlines() if '[ERROR]' in l]
        if errors:
            raise StepFailed(f'VQL error: {errors[0].split("[ERROR]", 1)[1].strip()}')
        return parse_json_arrays(result.stdout)

    def download(self, url, dest, attempts=5):
        self.log(f'Downloading {redact_url(url)}')
        tmp = dest.with_name(dest.name + '.part')
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                if urllib.parse.urlsplit(url).path.endswith('.gz'):
                    with gzip.open(tmp, 'rb') as src, open(dest, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    tmp.unlink()
                else:
                    tmp.replace(dest)
                return
            except (urllib.error.URLError, OSError) as e:
                if attempt == attempts:
                    raise StepFailed(f'Download of {redact_url(url)} failed: {e}')
                self.log(f'  attempt {attempt} failed ({e}), retrying...')
                time.sleep(5 * attempt)

    def url_exists(self, url):
        try:
            urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=30).close()
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def upload(self, path, url, attempts=5):
        for attempt in range(1, attempts + 1):
            try:
                with open(path, 'rb') as f:
                    req = urllib.request.Request(url, data=f, method='PUT', headers={
                        'x-ms-blob-type': 'BlockBlob',
                        'x-ms-version':   '2021-08-06',
                        'Content-Type':   'application/octet-stream',
                        'Content-Length': str(path.stat().st_size),
                    })
                    urllib.request.urlopen(req, timeout=600).close()
                return
            except (urllib.error.URLError, OSError) as e:
                if attempt == attempts:
                    raise StepFailed(f'Upload of {path.name} failed: {e}')
                self.log(f'  attempt {attempt} failed ({e}), retrying...')
                time.sleep(5 * attempt)

    def write_private(self, path, text):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(text)

    def release_url(self, name):
        '''URL of another asset from the same Velociraptor release.'''
        return self.p['velociraptor_url'].rsplit('/', 1)[0] + '/' + name

    def triage_targets(self):
        '''Windows.Triage.Targets is no longer built in; fetch the current definition.'''
        dest = self.artifacts / 'Windows.Triage.Targets.yaml'
        if not getattr(self, '_triage_fetched', False):
            zip_path = self.artifacts / 'Windows.Triage.Targets.zip'
            self.download(self.p['triage_targets_url'], zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                dest.write_bytes(zf.read('Windows.Triage.Targets.yaml'))
            zip_path.unlink()
            dest.chmod(0o644)
            self._triage_fetched = True
        return dest

    def wait_for_port(self, address, timeout=120):
        host, port = address.rsplit(':', 1)
        deadline = time.monotonic() + timeout
        while True:
            try:
                socket.create_connection((host, int(port)), timeout=5).close()
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise StepFailed(f'Velociraptor API did not come up on {address} within {timeout}s.')
                time.sleep(2)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def step_download(self):
        for dest, url in ((self.bin, self.p['velociraptor_url']), (self.bin_musl, self.musl_url)):
            if dest.exists():
                self.log(f'{dest.name} already present.')
                continue
            self.download(url, dest)
            dest.chmod(0o755)

    # Settings that are applied to an existing server config on a re-run.
    # Everything else is only set when the config is first generated.
    UPDATABLE_PATHS = ('/GUI/allowed_cidr', '/Frontend/resources/expected_clients')

    def server_patch(self):
        '''
        The JSON patch applied to a generated Velociraptor server config.

        The GUI and the frontend share port 443, so the GUI also gets the Let's
        Encrypt certificate that autocert requests for the DNS name. The GUI is
        then reachable from the internet, which makes GUI.allowed_cidr the
        access control for it. Everything is stored under the datastore
        directory on the OS disk, including the certificate cache, so the
        certificate survives restarts instead of being requested again.
        '''
        dns       = self.p['dns']
        datastore = self.p['datastore']
        cidrs     = [c if '/' in c else f'{c}/32' for c in self.p['gui_allowed_cidrs']]
        return [
            {'op': 'add',     'path': '/GUI/allowed_cidr',                    'value': cidrs},
            {'op': 'replace', 'path': '/Frontend/resources/expected_clients', 'value': int(self.p['expected_clients'])},
            {'op': 'replace', 'path': '/Frontend/hostname',                   'value': dns},
            {'op': 'replace', 'path': '/Client/server_urls',                  'value': [f'https://{dns}/']},
            {'op': 'add',     'path': '/GUI/public_url',                      'value': f'https://{dns}/app/index.html'},
            {'op': 'replace', 'path': '/GUI/bind_port',                       'value': 443},
            {'op': 'replace', 'path': '/Frontend/bind_port',                  'value': 443},
            {'op': 'add',     'path': '/autocert_cert_cache',                 'value': datastore},
            {'op': 'replace', 'path': '/Datastore/location',                  'value': datastore},
            {'op': 'replace', 'path': '/Datastore/filestore_directory',       'value': datastore},
            {'op': 'add',     'path': '/Logging/output_directory',            'value': f'{datastore}/logs'},
        ]

    def step_server_config(self):
        patch      = self.server_patch()
        patch_file = self.config.parent / 'patch.json'
        if self.config.exists():
            # Regenerating creates a new CA, which disconnects every client, so
            # only patch the settings that may change between runs.
            patch = [op for op in patch if op.get('path') in self.UPDATABLE_PATHS]
            patch_file.write_text(json.dumps(patch, indent=2))
            self.log(f'Updating existing {self.config}: {", ".join(op["path"] for op in patch)}')
            result = self.run([self.bin, '--config', self.config, 'config', 'show', '--patch_file', patch_file])
        else:
            patch_file.write_text(json.dumps(patch, indent=2))
            result = self.run([self.bin, 'config', 'generate', '--patch_file', patch_file])
        self.write_private(self.config, result.stdout)

    def step_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Not `velociraptor debian server`: in 0.76 it packages a freshly
            # generated default config instead of the given one. deb_create
            # without a config argument packages the config it runs under.
            self.run([self.bin, '--config', self.config, 'query', '--format', 'json',
                      '--env', f'Binary={self.bin}', '--env', f'Output={tmp}',
                      'SELECT OSPath FROM deb_create(exe=Binary, server=TRUE, directory_name=Output)'])
            debs = list(Path(tmp).glob('*.deb'))
            if len(debs) != 1:
                raise StepFailed(f'Expected one server package, found {len(debs)}.')
            self.run(['dpkg', '-i', debs[0]])

        # deb_create stores the config as it is while running a query, which
        # includes the reduced service list a query starts. With that list the
        # frontend mounts the GUI twice on the shared port and panics
        # ('pattern "/" conflicts with pattern "/"'), so drop it: without it
        # the frontend starts all services as usual. Writing to the existing
        # file keeps the owner and mode the package set.
        installed = Path(self.p['installed_config'])
        text, count = re.subn(r'^services:\n(?:[ \t]+.*\n)*', '', installed.read_text(), flags=re.MULTILINE)
        if count:
            installed.write_text(text)
            self.log(f'Removed the query service list from {installed}.')

    def step_client_config(self):
        config = self.run([self.bin, '--config', self.config, 'config', 'client']).stdout
        writeback = self.p['writeback_windows']
        if writeback:
            # A JSON string is a valid YAML double-quoted string, backslashes included.
            config, count = re.subn(r'^(\s*writeback_windows:).*$',
                                    lambda m: f'{m.group(1)} {json.dumps(writeback)}',
                                    config, flags=re.MULTILINE)
            if count != 1:
                raise StepFailed('writeback_windows not found in the generated client config.')
        self.write_private(self.client_config, config)

    def step_client_packages(self):
        prefix = self.p['package_prefix']
        for old in self.packages.iterdir():
            old.unlink()

        for arch, key, release_name in (
            ('x64', 'msi_amd64_url', f'velociraptor-v{self.version}-windows-amd64.msi'),
            ('x86', 'msi_x86_url',   f'velociraptor-v{self.version}-windows-386.msi'),
        ):
            custom = self.p[key]
            url    = custom or self.release_url(release_name)
            # Releases from 0.77 no longer include a 32-bit MSI; without a
            # custom template there is then nothing to build the x86 installer from.
            if not custom and not self.url_exists(url):
                self.log(f'MSI template {arch}: none in the {self.version} release, skipping the {arch} installer.')
                continue
            self.log(f'MSI template {arch}: {"custom" if custom else "default (Velociraptor release)"}')
            with tempfile.TemporaryDirectory() as tmp:
                template = Path(tmp) / 'template.msi'
                self.download(url, template)
                self.run([self.bin, 'config', 'repack', '--msi', template, self.client_config,
                          self.packages / f'{prefix}_client_windows_install_{arch}.msi'])

        for binary, flavour in ((self.bin, ''), (self.bin_musl, 'musl_')):
            for kind, ext in (('debian', 'deb'), ('rpm', 'rpm')):
                with tempfile.TemporaryDirectory() as tmp:
                    self.run([binary, '--config', self.client_config, kind, 'client', '--output', tmp])
                    built = list(Path(tmp).glob(f'*.{ext}'))
                    if len(built) != 1:
                        raise StepFailed(f'Expected one {ext} package, found {len(built)}.')
                    shutil.move(built[0], self.packages / f'{prefix}_{flavour}client_{kind}_linux_x64.{ext}')

    def step_permissions(self):
        '''
        Give the service user the datastore, log and certificate directories.
        Commands this script runs as root with the server config can create
        them owned by root, and the server package only fixes the top two
        levels of the datastore, so the server could not write there.
        Only entries with the wrong owner are changed, so a re-run over a large
        datastore stays quick.
        '''
        user = self.p['service_user']
        if not user:
            return
        uid, gid = pwd.getpwnam(user).pw_uid, grp.getgrnam(user).gr_gid
        config   = json.loads(self.run([self.bin, '--config', self.config, 'config', 'show', '--json']).stdout)
        paths    = {config.get('Datastore', {}).get('location'),
                    config.get('Datastore', {}).get('filestore_directory'),
                    config.get('Logging', {}).get('output_directory'),
                    config.get('autocert_cert_cache')}
        changed = 0
        for path in sorted(p for p in paths if p):
            os.makedirs(path, exist_ok=True)
            for root, dirs, files in os.walk(path):
                for entry in [root] + [os.path.join(root, n) for n in dirs + files]:
                    st = os.lstat(entry)
                    if (st.st_uid, st.st_gid) != (uid, gid):
                        os.lchown(entry, uid, gid)
                        changed += 1
        self.log(f'{", ".join(sorted(p for p in paths if p))} owned by {user} ({changed} entries fixed).')

    def step_api_client(self):
        api_config = Path(self.p['api_config'])
        if api_config.exists():
            self.log(f'Keeping existing {api_config}.')
            return
        # Run as the service user: this writes the API user into the datastore.
        self.run([self.bin, '--config', self.p['installed_config'], 'config', 'api_client',
                  '--name', self.p['api_name'], '--role', 'administrator', api_config],
                 user=self.p['service_user'])
        text = re.sub(r'^api_connection_string:.*$', f'api_connection_string: {self.p["api_address"]}',
                      api_config.read_text(), flags=re.MULTILINE)
        api_config.write_text(text)

    def step_start(self):
        self.run(['systemctl', 'restart', 'velociraptor_server'])
        try:
            self.wait_for_port(self.p['api_address'])
        except StepFailed as e:
            # The cause is in the service output, which the extension result
            # would not show otherwise: log the tail and name the last error.
            status  = self.run(['systemctl', 'status', 'velociraptor_server', '--no-pager', '-l'], check=False)
            journal = self.run(['journalctl', '-u', 'velociraptor_server', '-n', '40', '--no-pager'], check=False)
            lines   = (status.stdout.strip().splitlines()[:4] + ['...']
                       + journal.stdout.strip().splitlines()[-25:])
            for line in lines:
                self.log('  | ' + line)
            errors = [l for l in journal.stdout.splitlines() if re.search(r'error|fatal|panic', l, re.IGNORECASE)]
            raise StepFailed(f'{e} Last service error: {errors[-1].strip() if errors else "none logged"}') from None

    def step_certificate(self):
        '''
        Check that the server presents a publicly trusted certificate for its
        DNS name. Velociraptor gets it from Let's Encrypt (autocert) on the
        first TLS connection, and clients refuse to connect without it, so a
        failed issuance (e.g. a rate limit) must stop the setup here.
        '''
        host, port = self.p['tls_check_address'].rsplit(':', 1)
        context    = ssl.create_default_context()
        deadline   = time.monotonic() + int(self.p['tls_check_timeout'])
        while True:
            try:
                with socket.create_connection((host, int(port)), timeout=30) as sock:
                    with context.wrap_socket(sock, server_hostname=self.p['dns']) as tls:
                        cert = tls.getpeercert()
                issuer = dict(item[0] for item in cert.get('issuer', ()))
                self.log(f'Certificate for {self.p["dns"]} issued by {issuer.get("organizationName", "?")}, '
                         f'valid until {cert.get("notAfter")}.')
                return
            except (ssl.SSLError, OSError) as e:
                if time.monotonic() > deadline:
                    raise StepFailed(
                        f'No trusted certificate for {self.p["dns"]} ({e}). Let\'s Encrypt issuance failed; '
                        'see the autocert errors in /opt/velociraptor/logs.')
                self.log(f'  no trusted certificate yet ({e}), retrying...')
                time.sleep(10)

    def step_admin_user(self):
        self.vql('SELECT user_create(user=AdminUser, password=AdminPassword, roles=["administrator"]) FROM scope()',
                 env={'AdminUser': self.p['admin_user'], 'AdminPassword': self.p['admin_password']},
                 secret=('AdminPassword',))
        self.result['admin_user'] = self.p['admin_user']

    def step_artifacts(self):
        # Windows.Triage.Targets first: the triage upload artifact builds on it.
        paths = [self.triage_targets()]
        for name in (BACKUP_ARTIFACT, TRIAGE_UPLOAD_ARTIFACT):
            path = self.artifacts / f'{name}.yaml'
            shutil.copy(self.files / f'{name}.yaml', path)
            path.chmod(0o644)
            paths.append(path)
        for path in paths:
            rows = self.vql('SELECT artifact_set(definition=read_file(filename=Path, length=10000000)).name AS Name '
                            'FROM scope()', env={'Path': path})
            if not rows or not rows[0].get('Name'):
                raise StepFailed(f'Importing {path.name} failed.')
            self.log(f'Imported {rows[0]["Name"]}.')

    def step_server_monitoring(self):
        if not self.p['backup_sas_url']:
            self.log(f'No backup_sas_url, not adding {BACKUP_ARTIFACT} to server monitoring.')
            return
        # Remove first so a re-run replaces the parameters instead of adding a second entry.
        self.vql(f'''
            SELECT rm_server_monitoring(artifact="{BACKUP_ARTIFACT}") FROM scope()
            SELECT add_server_monitoring(artifact="{BACKUP_ARTIFACT}",
                parameters=dict(SasURL=SasURL, ZipPassword=ZipPassword)) FROM scope()''',
                 env={'SasURL': self.p['backup_sas_url'], 'ZipPassword': self.p['backup_zip_password']},
                 secret=('SasURL', 'ZipPassword'))
        self.result['backup_azure'] = True

    def step_hunts(self):
        custom = self.p['hunts_url']
        if custom:
            path = self.work / 'hunts-custom.vql'
            self.download(custom, path)
        else:
            path = self.files / 'hunts.vql'
        self.log(f'Hunts: {"custom (hunts_url)" if custom else "default (hunts.vql)"}')
        hunts  = path.read_text()
        digest = hashlib.sha256(hunts.encode()).hexdigest()
        marker = self.state / 'hunts.sha256'
        if marker.exists() and marker.read_text() == digest:
            self.log('Hunts already created from this hunts file.')
            return
        # The hunts file can use these to send collections straight to the
        # Cycas input container, like Server.Utils.BackupAzure does.
        rows = self.vql(hunts, env={'TriageSasURL':      self.p['backup_sas_url'],
                                    'TriageZipPassword': self.p['backup_zip_password']},
                        secret=('TriageSasURL', 'TriageZipPassword'))
        marker.write_text(digest)
        self.log(f'{len(rows)} hunt(s) created.')

    def step_upload(self):
        sas = self.p['upload_sas_url']
        if not sas:
            self.log('No upload_sas_url, packages stay in ' + str(self.work))
            return
        base, _, token = sas.partition('?')
        uploaded = []
        for path in sorted(self.packages.iterdir()):
            name = f'{self.p["upload_prefix"]}/ClientInstallationPackages/{path.name}'
            self.log(f'Uploading {name}')
            self.upload(path, f'{base}/{urllib.parse.quote(name)}?{token}')
            uploaded.append(name)
        self.result['uploaded'] = uploaded

    def step_config_backup(self):
        '''
        Stage the server and client config for easy_install.py, which moves
        them to Key Vault and deletes the blobs. The VM has no identity to
        write to Key Vault itself, and the extension output is too short to
        carry them back.
        '''
        sas = self.p['upload_sas_url']
        if not sas:
            self.log('No upload_sas_url, configs are not backed up.')
            return
        base, _, token = sas.partition('?')
        staged = []
        for path in (self.config, self.client_config):
            name = f'{self.p["upload_prefix"]}/config-backup/{path.name}'
            self.log(f'Staging {name}')
            self.upload(path, f'{base}/{urllib.parse.quote(name)}?{token}')
            staged.append(name)
        self.result['config_backup'] = staged

    STEPS = [
        ('download',          step_download),
        ('server-config',     step_server_config),
        ('install',           step_install),
        ('client-config',     step_client_config),
        ('client-packages',   step_client_packages),
        ('permissions',       step_permissions),
        ('api-client',        step_api_client),
        ('start',             step_start),
        ('certificate',       step_certificate),
        ('admin-user',        step_admin_user),
        ('artifacts',         step_artifacts),
        ('server-monitoring', step_server_monitoring),
        ('hunts',             step_hunts),
        ('upload',            step_upload),
        ('config-backup',     step_config_backup),
    ]


def load_params(path):
    if path:
        params = json.loads(Path(path).read_text())
    else:
        encoded = os.environ.get('CYCAS_BOOTSTRAP_PARAMS')
        if not encoded:
            raise StepFailed('No parameters: pass --params or set CYCAS_BOOTSTRAP_PARAMS.')
        params = json.loads(base64.b64decode(encoded))
    missing = [k for k in REQUIRED if not params.get(k)]
    if missing:
        raise StepFailed(f'Missing parameters: {", ".join(missing)}')
    return {**DEFAULTS, **params}


def main():
    names  = [name for name, _ in Bootstrap.STEPS]
    parser = argparse.ArgumentParser(description='Set up a Velociraptor server.')
    parser.add_argument('--params', help='JSON parameter file (default: base64 JSON in CYCAS_BOOTSTRAP_PARAMS)')
    parser.add_argument('--only', help=f'comma-separated steps to run: {",".join(names)}')
    args = parser.parse_args()

    selected = args.only.split(',') if args.only else names
    unknown  = [s for s in selected if s not in names]
    if unknown:
        parser.error(f'unknown step(s): {", ".join(unknown)}')

    try:
        bootstrap = Bootstrap(load_params(args.params))
    except StepFailed as e:
        print(f'CYCAS_BOOTSTRAP_FAILED step=init: {e}', flush=True)
        return 1

    for name, fn in Bootstrap.STEPS:
        if name not in selected:
            continue
        bootstrap.log(f'== {name}')
        try:
            fn(bootstrap)
        except Exception as e:
            bootstrap.log(traceback.format_exc(), console=False)
            bootstrap.log(f'CYCAS_BOOTSTRAP_FAILED step={name}: {e}')
            return 1

    bootstrap.log('CYCAS_BOOTSTRAP_RESULT ' + json.dumps(bootstrap.result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
