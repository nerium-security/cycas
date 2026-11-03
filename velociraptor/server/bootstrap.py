#!/usr/bin/env python3
'''
Set up a Velociraptor server on an Ubuntu VM.

Runs on the VM as root, started by the Azure Custom Script Extension that
easy_install.py configures. Uses the standard library only, so it needs
nothing beyond the python3 that ships with Ubuntu.

Parameters are JSON, passed base64-encoded in CYCAS_BOOTSTRAP_PARAMS or read
from a file with --params. Required: dns, admin_user, admin_password,
gui_allowed_cidrs, velociraptor_url. See DEFAULTS for the optional ones.

The files it uses (hunts.vql, Server.Utils.BackupAzure.yaml,
Custom.Windows.Triage.Targets.AzureUpload.yaml and
Server.Monitor.Autolabeling.Clients.yaml)
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
import io
import json
import os
import plistlib
import posixpath
import pwd
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import uuid
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

DEFAULTS = {
    'expected_clients':    1000,
    'package_prefix':      'Velociraptor',
    'api_name':            'cycas_api',
    'download_password':   'infected',   # default ZIP password for GUI exports; empty: none
    'users':               [],           # extra GUI users: [{name, role, password}]
    'msi_amd64_url':       '',   # empty: the MSI from the Velociraptor release
    'msi_x86_url':         '',
    'linux_client_name':   '',   # empty: stock velociraptor_client deb/rpm only; set: also build packages renamed to this
    'hunts_url':           '',   # empty: hunts.vql from files_dir
    'writeback_windows':   '',   # empty: keep the Velociraptor default
    'triage_targets_url':  'https://triage.velocidex.com/artifacts/Windows.Triage.Targets.zip',
    'upload_sas_url':      '',   # container SAS with write access for the client packages; empty: no upload
    'installers_prefix':   'Velociraptor-ClientInstallers',
    'staging_sas_url':     '',   # container SAS with write access for the config backup; empty: no backup
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
AUTOLABEL_ARTIFACT     = 'Server.Monitor.Autolabeling.Clients'


# Uploaded next to the legacy client executables. CRLF line endings, as it is
# read on Windows endpoints.
LEGACY_README = '''Velociraptor legacy clients
===========================

For Windows 7/8, Server 2008 R2/2012 and 32-bit Windows, which the MSI does
not support. Velociraptor publishes no MSI for these builds, so the client
is an executable with the client configuration embedded:

{files}

x64: 64-bit Windows 7/8 and Server 2008 R2/2012.
x86: 32-bit Windows (check with: echo %PROCESSOR_ARCHITECTURE%).

Install
-------
Copy the executable to the endpoint and run in an elevated command prompt:

  <file>.exe service install

This copies the client to C:\\Program Files\\Velociraptor\\Velociraptor.exe and
installs and starts the "Velociraptor" service. The endpoint shows up in the
Velociraptor GUI within a minute. Check the service with:

  sc query Velociraptor

For mass deployment (GPO startup script, SCCM, PsExec) run the same command.

Uninstall
---------
  "C:\\Program Files\\Velociraptor\\Velociraptor.exe" service remove
'''


# The macOS client package holds the release executables unchanged, so their
# code signature stays valid (repacking the config into them would break it),
# next to client.config.yaml, with install/uninstall scripts. Placeholders:
# @NAME@, filled in by fill().

MACOS_INSTALL_SH = '''#!/bin/sh
# Install the Velociraptor client as the launchd service @SERVICE@.
# Run: sudo ./install.sh
set -e
cd "$(dirname "$0")"
if [ "$(id -u)" != 0 ]; then
  echo "Run with sudo: sudo ./install.sh" >&2
  exit 1
fi
case "$(uname -m)" in
  arm64)  binary=velociraptor-arm64 ;;
  x86_64) binary=velociraptor-amd64 ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
# Downloaded with a browser, the files are quarantined and blocked.
xattr -d com.apple.quarantine "$binary" client.config.yaml 2>/dev/null || true
chmod 755 "$binary"
# On a re-run, replace the running service.
if [ -f "@PLIST@" ]; then
  launchctl unload -w "@PLIST@" 2>/dev/null || true
fi
"./$binary" service install --config client.config.yaml -v
# Velociraptor copies the config world-readable; only root needs it.
chown root:wheel "@CONFIG_PATH@"
chmod 600 "@CONFIG_PATH@"
echo "Installed. Check with: sudo launchctl print system/@SERVICE@"
'''

MACOS_UNINSTALL_SH = '''#!/bin/sh
# Remove the Velociraptor client. Run: sudo ./uninstall.sh [--purge]
#   without --purge the client identity (writeback) is kept, so a
#   reinstall shows up as the same client; --purge removes it as well.
if [ "$(id -u)" != 0 ]; then
  echo "Run with sudo: sudo ./uninstall.sh" >&2
  exit 1
fi
launchctl unload -w "@PLIST@" 2>/dev/null || true
rm -f "@PLIST@" "@INSTALL_PATH@" "@CONFIG_PATH@"
if [ "$1" = "--purge" ]; then
  rm -f "@WRITEBACK@"
fi
echo "Velociraptor client removed."
'''

MACOS_README = '''Velociraptor macOS client
=========================

Velociraptor @VERSION@, connects to @SERVER_URL@

  @PREFIX@_client_macos.tar.gz                    the client, Apple Silicon and Intel
  @PREFIX@_client_macos_FullDiskAccess.mobileconfig  Full Disk Access profile for MDM

Velociraptor publishes no .pkg for macOS. The archive holds the release
executables (signed by Velocidex, Apple team UL6CGN7MAL, unchanged) for
Apple Silicon and Intel, the client configuration, and install scripts that
use Velociraptor's own service installer. install.sh picks the executable
that matches the Mac.

Install
-------
On the Mac, in Terminal:

  tar xzf @PREFIX@_client_macos.tar.gz
  cd velociraptor-client
  sudo ./install.sh

This copies the executable to @INSTALL_PATH@ and the config to
@CONFIG_PATH@ (readable by root only), and installs and starts
the launchd service @SERVICE@ (@PLIST@).
The Mac shows up in the Velociraptor GUI within a minute.

If macOS still blocks the executable: System Settings > Privacy & Security,
click "Allow Anyway", and run install.sh again.

For MDM (Jamf, Intune, ...): deploy the extracted folder and run install.sh
as root from a script/policy.

Full Disk Access
----------------
Without Full Disk Access, macOS (10.14 and later) hides protected files and
folders from the client, and many artifacts return little or nothing.
Grant it with the Privacy Preferences Policy Control (PPPC) profile
@PREFIX@_client_macos_FullDiskAccess.mobileconfig, deployed through MDM to
the same Macs. macOS only accepts this kind of profile from an MDM; it
cannot be installed by double-clicking it.

The profile allows SystemPolicyAllFiles for @INSTALL_PATH@ when it is
signed by Velocidex (team UL6CGN7MAL). It does not require a signing
identifier: the release builds are not all signed with the same one.

Check: run the MacOS.System.TCC artifact on the Mac. Empty output means no
Full Disk Access. System Settings may show velociraptor as disabled under
Full Disk Access even when the MDM profile grants it.

Check
-----
  sudo launchctl print system/@SERVICE@
  ps -eaf | grep velociraptor

Upgrade
-------
Upgrade clients from the GUI with a hunt for Admin.Client.Upgrade.MacOS,
rather than running install.sh again.

Uninstall
---------
  sudo ./uninstall.sh           keeps the client identity, a reinstall shows
                                up as the same client in the GUI
  sudo ./uninstall.sh --purge   also removes the client identity
                                (@WRITEBACK@)

Velociraptor's service installer bypasses a package manager, which is why
uninstall.sh removes the installed files itself.
'''

# Velocidex's Apple Developer ID team. The docs' code requirement also checks
# identifier "com.velocidex.velociraptor", but the release builds are signed
# with other identifiers (0.77.3: "a.out" and "velociraptor-v0"), so it would
# never match: only the Developer ID chain and the team are required.
VELOCIDEX_TEAM_ID = 'UL6CGN7MAL'
MACOS_CODE_REQUIREMENT = (
    'anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] /* exists */ '
    'and certificate leaf[field.1.2.840.113635.100.6.1.13] /* exists */ '
    f'and certificate leaf[subject.OU] = "{VELOCIDEX_TEAM_ID}"')


def fill(template, **values):
    '''Replace each @NAME@ in template by values['name'].'''
    return re.sub(r'@([A-Z_]+)@', lambda m: str(values[m.group(1).lower()]), template)


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
                        'Content-Type':   'text/plain; charset=utf-8' if path.suffix == '.txt'
                                          else 'application/octet-stream',
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

    def macos_values(self, prefix):
        '''Placeholder values for the macOS scripts and README, from the client config.'''
        client = json.loads(self.run([self.bin, '--config', self.client_config,
                                      'config', 'show', '--json']).stdout)['Client']
        mac    = client['darwin_installer']
        return {
            'version':      self.version,
            'server_url':   f'https://{self.p["dns"]}/',
            'prefix':       prefix,
            'service':      mac['service_name'],
            'install_path': mac['install_path'],
            # Velociraptor's service installer copies the config next to the
            # executable, with the extension replaced by .config.yaml.
            'config_path':  posixpath.splitext(mac['install_path'])[0] + '.config.yaml',
            'plist':        f'/Library/LaunchDaemons/{mac["service_name"]}.plist',
            'writeback':    client['writeback_darwin'],
        }

    def release_binaries(self, tmp, builds):
        '''Download release executables {build: file name} into tmp; None when
        one of them is not in this release.'''
        paths = {}
        for build, name in builds.items():
            url = self.release_url(f'velociraptor-v{self.version}-{build}.gz')
            if not self.url_exists(url):
                return None
            paths[build] = Path(tmp) / name
            self.download(url, paths[build])
        return paths

    def build_macos_package(self, standard, prefix):
        '''
        macOS has no installer package: a tar.gz with the signed release
        executables for Apple Silicon and Intel, client.config.yaml and
        install/uninstall scripts, plus a Full Disk Access (PPPC) profile
        for MDM. The README goes inside and next to the archive.
        '''
        mac = self.macos_values(prefix)
        with tempfile.TemporaryDirectory() as tmp:
            binaries = self.release_binaries(tmp, {'darwin-arm64': 'velociraptor-arm64',
                                                   'darwin-amd64': 'velociraptor-amd64'})
            if not binaries:
                self.log(f'macOS client: not in the {self.version} release, skipping it.')
                return
            readme = fill(MACOS_README, **mac)
            files  = [(binaries['darwin-arm64'].read_bytes(), 'velociraptor-arm64', 0o755),
                      (binaries['darwin-amd64'].read_bytes(), 'velociraptor-amd64', 0o755),
                      (self.client_config.read_bytes(),     'client.config.yaml', 0o600),
                      (fill(MACOS_INSTALL_SH, **mac).encode(),   'install.sh', 0o755),
                      (fill(MACOS_UNINSTALL_SH, **mac).encode(), 'uninstall.sh', 0o755),
                      (readme.encode(),                     'README.txt', 0o644)]
            output = standard / f'{prefix}_client_macos.tar.gz'
            with tarfile.open(output, 'w:gz') as tf:
                for data, name, mode in files:
                    info = tarfile.TarInfo(f'velociraptor-client/{name}')
                    info.size, info.mode, info.mtime = len(data), mode, int(time.time())
                    info.uname = info.gname = 'root'
                    tf.addfile(info, io.BytesIO(data))
        (standard / f'{prefix}_client_macos_README.txt').write_text(readme)
        (standard / f'{prefix}_client_macos_FullDiskAccess.mobileconfig').write_bytes(self.macos_fda_profile(mac))
        self.log(f'macOS client: {output.name}')

    def macos_fda_profile(self, mac):
        '''
        A PPPC configuration profile granting the client Full Disk Access
        (SystemPolicyAllFiles). The UUIDs derive from the server name, so a
        re-run produces the same profile and an MDM updates it in place.
        '''
        ident = f'app.velociraptor.cycas.fda.{self.p["dns"]}'
        uid   = lambda suffix: str(uuid.uuid5(uuid.NAMESPACE_DNS, f'{ident}.{suffix}')).upper()
        return plistlib.dumps({
            'PayloadType':         'Configuration',
            'PayloadVersion':      1,
            'PayloadIdentifier':   ident,
            'PayloadUUID':         uid('profile'),
            'PayloadDisplayName':  'Velociraptor Full Disk Access',
            'PayloadDescription':  f'Full Disk Access for the Velociraptor client ({mac["install_path"]}).',
            'PayloadScope':        'System',
            'PayloadContent': [{
                'PayloadType':        'com.apple.TCC.configuration-profile-policy',
                'PayloadVersion':     1,
                'PayloadIdentifier':  f'{ident}.tcc',
                'PayloadUUID':        uid('tcc'),
                'PayloadDisplayName': 'Velociraptor Full Disk Access',
                'Services': {'SystemPolicyAllFiles': [{
                    'Identifier':      mac['install_path'],
                    'IdentifierType':  'path',
                    'CodeRequirement': MACOS_CODE_REQUIREMENT,
                    'StaticCode':      False,
                    'Authorization':   'Allow',
                    'Comment':         'Velociraptor client',
                }]},
            }],
        })

    def repack_msi(self, template_url, output):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / 'template.msi'
            self.download(template_url, template)
            self.run([self.bin, 'config', 'repack', '--msi', template, self.client_config, output])

    def renamed_linux_spec(self, binary, create, name):
        '''
        The default <create> (deb_create/rpm_create) client package spec with
        every "velociraptor_client" name replaced by name: the installed binary,
        the systemd and SysV service, the config path, the service user and
        group, and the package filename. The paths inside the spec are templated
        off these Expansion fields, so renaming the fields renames everything.
        '''
        result = self.run([binary, '--config', self.client_config, 'query', '--format', 'json',
                           f'SELECT Spec FROM {create}(show_spec=TRUE)'])
        rows = parse_json_arrays(result.stdout)
        if not rows or not rows[0].get('Spec'):
            raise StepFailed(f'{create} show_spec returned no spec.')
        spec = rows[0]['Spec']
        spec['Expansion'].update({
            'Name':                   name,
            'ConfigPath':             f'/etc/{name}/client.config.yaml',
            'VelociraptorBinaryPath': f'/usr/local/bin/{name}',
            'SystemdServiceFile':     f'{name}.service',
            'SysvService':            name,
            'ServiceUser':            name,
            'ServiceGroup':           name,
            'ServiceDescription':     f'{name} endpoint agent',
        })
        spec['OutputFilenameTemplate'] = f'{name}_{{{{ .Version }}}}_{{{{ .Arch }}}}.{"deb" if create == "deb_create" else "rpm"}'
        return spec

    def build_renamed_linux_package(self, binary, ext, name, output):
        '''Build one client package (deb or rpm) renamed to name, into output.'''
        create = 'deb_create' if ext == 'deb' else 'rpm_create'
        spec   = json.dumps(self.renamed_linux_spec(binary, create, name))
        with tempfile.TemporaryDirectory() as tmp:
            self.run([binary, '--config', self.client_config, 'query', '--format', 'json',
                      '--env', f'Binary={binary}', '--env', f'Output={tmp}',
                      '--env', f'ConfigPath={self.client_config}', '--env', f'Spec={spec}',
                      f'SELECT OSPath FROM {create}(exe=Binary, directory_name=Output, '
                      f'config=read_file(filename=ConfigPath, length=1000000), '
                      f'package_spec=parse_json(data=Spec))'])
            built = list(Path(tmp).glob(f'*.{ext}'))
            if len(built) != 1:
                raise StepFailed(f'Expected one renamed {ext} package, found {len(built)}.')
            shutil.move(built[0], output)

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
            shutil.rmtree(old) if old.is_dir() else old.unlink()
        # Everything built from the Velociraptor release goes to standard/,
        # the MSIs built from a custom (obfuscated) template to custom/.
        standard, custom = self.packages / 'standard', self.packages / 'custom'
        standard.mkdir()

        for arch, key, release_name in (
            ('x64', 'msi_amd64_url', f'velociraptor-v{self.version}-windows-amd64.msi'),
            ('x86', 'msi_x86_url',   f'velociraptor-v{self.version}-windows-386.msi'),
        ):
            # Releases from 0.77 no longer include a 32-bit MSI; the standard
            # x86 installer is then skipped.
            url = self.release_url(release_name)
            if self.url_exists(url):
                self.log(f'MSI {arch}: standard (Velociraptor release template)')
                self.repack_msi(url, standard / f'{prefix}_client_windows_install_{arch}.msi')
            else:
                self.log(f'MSI {arch}: no template in the {self.version} release, skipping the standard {arch} installer.')
            if self.p[key]:
                self.log(f'MSI {arch}: custom (obfuscated template)')
                custom.mkdir(exist_ok=True)
                self.repack_msi(self.p[key], custom / f'{prefix}_client_windows_install_{arch}_custom.msi')

        # The legacy builds (Windows 7/8, Server 2008 R2/2012, and the only
        # 32-bit build from 0.77) have no MSI: the client config is repacked
        # into the executable, installed with `<exe> service install`.
        legacy = []
        for arch, build in (('x64', 'windows-amd64-legacy'), ('x86', 'windows-386-legacy')):
            url = self.release_url(f'velociraptor-v{self.version}-{build}.exe.gz')
            if not self.url_exists(url):
                self.log(f'Legacy client {arch}: none in the {self.version} release, skipping it.')
                continue
            with tempfile.TemporaryDirectory() as tmp:
                template = Path(tmp) / 'template.exe'
                self.download(url, template)
                output = standard / f'{prefix}_client_windows_install_{arch}_legacy.exe'
                self.run([self.bin, 'config', 'repack', '--exe', template, self.client_config, output])
                legacy.append(output.name)
        if legacy:
            (standard / f'{prefix}_client_windows_legacy_README.txt').write_text(
                LEGACY_README.format(files='\n'.join(f'  {name}' for name in legacy)).replace('\n', '\r\n'))

        self.build_macos_package(standard, prefix)


        # Linux: stock velociraptor_client deb/rpm, glibc and musl, into
        # standard/. With a custom name, an extra set is built into custom/
        # where the binary, service, paths and package all use that name.
        name = self.p['linux_client_name']
        for binary, flavour in ((self.bin, ''), (self.bin_musl, 'musl_')):
            for kind, ext in (('debian', 'deb'), ('rpm', 'rpm')):
                with tempfile.TemporaryDirectory() as tmp:
                    self.run([binary, '--config', self.client_config, kind, 'client', '--output', tmp])
                    built = list(Path(tmp).glob(f'*.{ext}'))
                    if len(built) != 1:
                        raise StepFailed(f'Expected one {ext} package, found {len(built)}.')
                    shutil.move(built[0], standard / f'{prefix}_{flavour}client_{kind}_linux_x64.{ext}')
                if name:
                    self.log(f'Linux {kind} {flavour}client: custom (renamed to {name})')
                    custom.mkdir(exist_ok=True)
                    self.build_renamed_linux_package(
                        binary, ext, name, custom / f'{name}_{flavour}client_{kind}_linux_x64.{ext}')

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
        # The admin user plus the extra users. user_create also updates an
        # existing user, so a re-run applies the role and (Key Vault) password
        # again. Users removed from the list are left alone, so a typo cannot
        # lock anyone out.
        #
        # The download password is the user preference the GUI sets ("Password"
        # in the user tile): ZIP exports from the GUI are protected with it, so
        # triage data does not end up unprotected on an analyst's machine.
        accounts = [{'name': self.p['admin_user'], 'role': 'administrator', 'password': self.p['admin_password']}]
        for user in accounts + list(self.p['users']):
            self.vql('SELECT user_create(user=UserName, password=UserPassword, roles=[UserRole]) FROM scope()'
                     '  SELECT user_options(user=UserName, default_password=DownloadPassword) FROM scope()',
                     env={'UserName': user['name'], 'UserPassword': user['password'], 'UserRole': user['role'],
                          'DownloadPassword': self.p['download_password']},
                     secret=('UserPassword', 'DownloadPassword'))
            self.log(f"User '{user['name']}' ({user['role']}) ready.")
        self.result['admin_user'] = self.p['admin_user']
        self.result['users']      = [user['name'] for user in self.p['users']]

    def step_artifacts(self):
        # Windows.Triage.Targets first: the triage upload artifact builds on it.
        paths = [self.triage_targets()]
        for name in (BACKUP_ARTIFACT, TRIAGE_UPLOAD_ARTIFACT, AUTOLABEL_ARTIFACT):
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
        # Labels new clients Workstation, Server, Domain Controller or Linux when
        # their interrogation finishes. Remove first (here and below) so a re-run
        # replaces the entry instead of adding a second one.
        self.vql(f'''
            SELECT rm_server_monitoring(artifact="{AUTOLABEL_ARTIFACT}") FROM scope()
            SELECT add_server_monitoring(artifact="{AUTOLABEL_ARTIFACT}") FROM scope()''')
        self.result['autolabeling'] = True

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
        for path in sorted(p for p in self.packages.rglob('*') if p.is_file()):
            name = f'{self.p["installers_prefix"]}/{path.relative_to(self.packages).as_posix()}'
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
        sas = self.p['staging_sas_url']
        if not sas:
            self.log('No staging_sas_url, configs are not backed up.')
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
