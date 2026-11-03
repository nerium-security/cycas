'''
Build the Cycas offline collectors: stand-alone Windows and Linux executables,
and a macOS collector package, that collect triage data and live data and
upload the encrypted result straight to the Cycas input container via a
write-only SAS URL. (macOS is run with the official Velociraptor darwin binary:
`<binary> -- --embedded_config <package>`; Apple notarization means Velociraptor
does not self-contain the darwin binary.)

Shared by easy_install.py and scripts/standalone/build_collector.py, so both
build the same collectors.
'''

import gzip
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

TRIAGE_TARGETS_URL = 'https://triage.velocidex.com/artifacts/Windows.Triage.Targets.zip'

# The Windows live data and its settings match the hunts on the Velociraptor
# server (velociraptor/server/hunts.vql): hashes and certificate info where the
# artifact offers them, potentially unstable Windows APIs disabled, and no
# executables (no process dumps, no task commands). Unlike the server, an
# offline collector cannot run separate hunts, so it collects everything.
#
# The Linux and MacOS sets are a starting point for triage and live state and
# should get the same DFIR review the Windows set had before being relied on.
# Each artifact maps to its parameters ({} keeps the artifact's defaults).
# 'template' is the built executable's name; 'purpose' labels the download.
COLLECTOR_PLATFORMS = {
    'Windows': {
        'template': 'OfflineCollector_Windows-amd64.exe',
        'purpose':  'Windows 10/11, Server 2016 and later (64-bit)',
        'artifacts': {
            'Windows.Triage.Targets':          {'HighLevelTargets': '["_KapeTriage"]'},
            'Windows.System.Pslist':           {'DISABLE_DANGEROUS_API_CALLS': 'Y'},
            'Windows.Network.NetstatEnriched': {'ProcessNameRegex': '.', 'DumpProcess': 'N', 'KillProcess': 'N',
                                                'DISABLE_DANGEROUS_API_CALLS': 'Y'},
            'Windows.System.Services':         {'Calculate_hashes': 'Y', 'CertificateInfo': 'Y',
                                                'DISABLE_DANGEROUS_API_CALLS': 'Y'},
            'Windows.System.DNSCache':         {},
            'Windows.System.TaskScheduler':    {'AlsoUpload': 'N', 'UploadCommands': 'N'},
        },
    },
    'Linux': {
        'template': 'OfflineCollector_Linux',
        'purpose':  'Linux (64-bit / amd64, statically linked)',
        'artifacts': {
            'Linux.Sys.Pslist':              {},   # running processes
            'Linux.Network.NetstatEnriched': {},   # network connections
            'Linux.Sys.Services':            {},   # systemd services
            'Linux.Sys.Crontab':             {},   # cron persistence
            'Linux.Sys.BashHistory':         {},   # shell history
            'Linux.Ssh.AuthorizedKeys':      {},   # SSH access / persistence
            'Linux.Sys.SUID':                {},   # setuid binaries
            'Linux.Sys.Users':               {},   # local accounts
            'Linux.Proc.Modules':            {},   # loaded kernel modules
            'Linux.Syslog.SSHLogin':         {},   # SSH login events
        },
    },
    'MacOS': {
        'template': 'OfflineCollector_MacOS',
        # Velociraptor does not self-contain the (notarized) macOS binary: this
        # is a config package, run with the official darwin binary from the
        # Velociraptor GitHub releases:  <binary> -- --embedded_config <file>
        'purpose':  'macOS (config package; run with the official Velociraptor darwin binary)',
        'artifacts': {
            'MacOS.Sys.Pslist':               {},   # running processes
            'MacOS.Network.Netstat':          {},   # network connections
            'MacOS.Detection.Autoruns':       {},   # launch agents/daemons, cron, login items
            'MacOS.Detection.InstallHistory': {},   # package install history
            'MacOS.System.Packages':          {},   # installed packages
            'MacOS.System.TCC':               {},   # privacy (TCC) permissions
            'MacOS.System.QuarantineEvents':  {},   # downloaded-file provenance
            'MacOS.System.Users':             {},   # local accounts
            'MacOS.Applications.MRU':         {},   # recently used items
        },
    },
}


# Velociraptor's legacy builds use an older Go version that still runs on
# Windows 7/8 and Server 2008 R2/2012. They are also the only 32-bit builds:
# releases from 0.77 have no regular 32-bit Windows build.
LEGACY_BUILDS = {
    'windows-amd64-legacy': 'Windows 7/8, Server 2008 R2/2012 (64-bit)',
    'windows-386-legacy':   'all 32-bit Windows, including Windows 7',
}


class CollectorBuildError(RuntimeError):
    pass


def _yaml_str(value):
    '''A JSON string is a valid YAML double-quoted string, whatever it contains.'''
    return json.dumps(str(value))


def write_collector_spec(spec_path, os_name, sas_url, password):
    '''Write a velociraptor `collector` spec for os_name's artifact set that
    uploads the password-encrypted result via the SAS URL.'''
    platform  = COLLECTOR_PLATFORMS[os_name]
    artifacts = []
    for name, params in platform['artifacts'].items():
        if params:
            artifacts.append(f'  {name}:')
            artifacts += [f'    {key}: {_yaml_str(value)}' for key, value in params.items()]
        else:
            artifacts.append(f'  {name}: {{}}')
    nl = '\n'

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(f'''OS: {os_name}

Artifacts:
{nl.join(artifacts)}

Target: Azure
TargetArgs:
  sas_url: {_yaml_str(sas_url)}

EncryptionScheme: Password
EncryptionArgs:
  password: {_yaml_str(password)}

OptVerbose: true
OptBanner: true
OptPrompt: false
OptAdmin: true
OptLevel: 4
OptConcurrency: 2
OptFormat: "jsonl"
OptCollectorTemplate: {_yaml_str(platform['template'])}
OptFilenameTemplate: "Triage-%FQDN%-%TIMESTAMP%"
OptProgressTimeout: 1800
OptTimeout: 0
OptDeleteAtExit: true
''')


def download_triage_targets(dest_dir):
    '''Download and extract the latest Windows.Triage.Targets artifact definition.'''
    with urlopen(TRIAGE_TARGETS_URL) as resp:
        data = resp.read()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_dir)


def build_offline_collector(binary, build_dir, sas_url, password, os_name='Windows'):
    '''
    Build the offline collector for os_name ('Windows', 'Linux' or 'MacOS') and
    return the path of the executable that was just built.

    Each OS builds in its own datastore under build_dir, so the collectors do
    not overwrite each other. The datastore is emptied first: velociraptor names
    the collector after OptCollectorTemplate, so a stale build would otherwise
    be mistaken for this one.

    Raises:
        CollectorBuildError: When a step fails; the message says which.
    '''
    if os_name not in COLLECTOR_PLATFORMS:
        raise CollectorBuildError(f'Unknown collector OS {os_name!r}.')
    platform  = COLLECTOR_PLATFORMS[os_name]
    build_dir = Path(build_dir) / os_name.lower()
    datastore = build_dir / 'datastore'
    spec_path = build_dir / 'spec.yaml'
    shutil.rmtree(datastore, ignore_errors=True)

    # Only Windows needs the external Windows.Triage.Targets definition; the
    # Linux and MacOS artifacts ship with the Velociraptor binary.
    if os_name == 'Windows':
        try:
            download_triage_targets(datastore / 'artifact_definitions' / 'Windows' / 'Triage')
        except Exception as e:
            raise CollectorBuildError(f'Could not download Windows.Triage.Targets: {e}') from None
    else:
        datastore.mkdir(parents=True, exist_ok=True)

    write_collector_spec(spec_path, os_name, sas_url, password)

    result = subprocess.run([str(binary), 'collector', '--datastore', str(datastore), str(spec_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise CollectorBuildError((result.stderr.strip().splitlines() or ['Unknown error.'])[-1])

    built = [p for p in datastore.glob(platform['template'] + '*') if p.is_file()]
    if len(built) != 1:
        raise CollectorBuildError(f'Expected one {os_name} collector in {datastore}, found {len(built)}.')
    return built[0]


def build_legacy_collectors(binary, collector, velociraptor_url, build_dir):
    '''
    Build the legacy collectors (LEGACY_BUILDS) from a regular collector.

    A collector is a Velociraptor executable with a configuration embedded, so
    the configuration of the regular collector is repacked into the legacy
    builds of the same release: same artifacts, settings, SAS URL and ZIP
    password. (The spec option OptCollectorTemplate only changes the name of
    the output, not the executable it is built from.)

    Returns:
        list[tuple[Path, str]]: Each legacy collector with what it is for.

    Raises:
        CollectorBuildError: When a step fails; the message says which.
    '''
    match = re.search(r'/download/v([\d.]+)/velociraptor-v[\d.]+-linux-amd64', velociraptor_url or '')
    if not match:
        raise CollectorBuildError(f'Cannot derive the release from VELOCIRAPTOR_URL: {velociraptor_url!r}.')
    version, base = match.group(1), velociraptor_url.rsplit('/', 1)[0]

    out_dir = Path(build_dir) / 'legacy'
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)

    built = []
    # The embedded configuration holds the SAS URL and ZIP password: keep it
    # in a temporary directory that is removed afterwards.
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / 'collector.config.yaml'
        result = subprocess.run([str(binary), 'config', 'show', '--embedded_config', str(collector)],
                                capture_output=True, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            raise CollectorBuildError(f'Could not read the configuration of {Path(collector).name}.')
        config.write_text(result.stdout)

        for build, purpose in LEGACY_BUILDS.items():
            name     = f'velociraptor-v{version}-{build}.exe'
            template = Path(tmp) / name
            try:
                with urlopen(f'{base}/{name}.gz') as resp:
                    template.write_bytes(gzip.decompress(resp.read()))
            except Exception as e:
                raise CollectorBuildError(f'Could not download {name}.gz: {e}') from None

            output = out_dir / f'OfflineCollector_{build[0].upper()}{build[1:]}.exe'   # e.g. _Windows-amd64-legacy.exe
            result = subprocess.run([str(binary), 'config', 'repack', '--exe', str(template), str(config), str(output)],
                                    capture_output=True, text=True)
            if result.returncode != 0 or not output.exists():
                raise CollectorBuildError(f'Repacking {name} failed: '
                                          f'{(result.stderr.strip().splitlines() or ["Unknown error."])[-1]}')
            built.append((output, purpose))
    return built
