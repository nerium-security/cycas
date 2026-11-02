'''
Build the Cycas offline collector: a stand-alone Windows executable that
collects triage data and live data, and uploads the encrypted result straight
to the Cycas input container via a write-only SAS URL.

Shared by easy_install.py and scripts/standalone/build_collector.py, so both
build the same collector.
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

# The live data and its settings match the hunts on the Velociraptor server
# (velociraptor/server/hunts.vql): hashes and certificate info where the
# artifact offers them, potentially unstable Windows APIs disabled, and no
# executables (no process dumps, no task commands). Unlike the server, an
# offline collector cannot run separate hunts, so it collects everything.
COLLECTOR_ARTIFACTS = {
    'Windows.Triage.Targets':          {'HighLevelTargets': '["_KapeTriage"]'},
    'Windows.System.Pslist':           {'DISABLE_DANGEROUS_API_CALLS': 'Y'},
    'Windows.Network.NetstatEnriched': {'ProcessNameRegex': '.', 'DumpProcess': 'N', 'KillProcess': 'N',
                                        'DISABLE_DANGEROUS_API_CALLS': 'Y'},
    'Windows.System.Services':         {'Calculate_hashes': 'Y', 'CertificateInfo': 'Y',
                                        'DISABLE_DANGEROUS_API_CALLS': 'Y'},
    'Windows.System.DNSCache':         {},
    'Windows.System.TaskScheduler':    {'AlsoUpload': 'N', 'UploadCommands': 'N'},
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


def write_collector_spec(spec_path, sas_url, password):
    '''Write a velociraptor `collector` spec for COLLECTOR_ARTIFACTS that uploads
    the password-encrypted result via the SAS URL.'''
    artifacts = []
    for name, params in COLLECTOR_ARTIFACTS.items():
        if params:
            artifacts.append(f'  {name}:')
            artifacts += [f'    {key}: {_yaml_str(value)}' for key, value in params.items()]
        else:
            artifacts.append(f'  {name}: {{}}')
    nl = '\n'

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(f'''OS: Windows

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


def build_offline_collector(binary, build_dir, sas_url, password):
    '''
    Build the offline collector in build_dir and return the path of the
    executable that was just built.

    The datastore folder is emptied first: velociraptor writes the collector
    there under a name that only contains its version, so an older build would
    otherwise be mistaken for this one.

    Raises:
        CollectorBuildError: When a step fails; the message says which.
    '''
    build_dir = Path(build_dir)
    datastore = build_dir / 'datastore'
    spec_path = build_dir / 'spec.yaml'
    shutil.rmtree(datastore, ignore_errors=True)

    try:
        download_triage_targets(datastore / 'artifact_definitions' / 'Windows' / 'Triage')
    except Exception as e:
        raise CollectorBuildError(f'Could not download Windows.Triage.Targets: {e}') from None

    write_collector_spec(spec_path, sas_url, password)

    result = subprocess.run([str(binary), 'collector', '--datastore', str(datastore), str(spec_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise CollectorBuildError((result.stderr.strip().splitlines() or ['Unknown error.'])[-1])

    built = sorted(datastore.glob('Collector_*'))
    if len(built) != 1:
        raise CollectorBuildError(f'Expected one collector in {datastore}, found {len(built)}.')
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

            output = out_dir / f'Collector_{name}'
            result = subprocess.run([str(binary), 'config', 'repack', '--exe', str(template), str(config), str(output)],
                                    capture_output=True, text=True)
            if result.returncode != 0 or not output.exists():
                raise CollectorBuildError(f'Repacking {name} failed: '
                                          f'{(result.stderr.strip().splitlines() or ["Unknown error."])[-1]}')
            built.append((output, purpose))
    return built
