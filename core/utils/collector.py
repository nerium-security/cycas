'''
Build the Cycas offline collector: a stand-alone Windows executable that
collects triage data and live data, and uploads the encrypted result straight
to the Cycas input container via a write-only SAS URL.

Shared by easy_install.py and scripts/standalone/build_collector.py, so both
build the same collector.
'''

import io
import json
import shutil
import subprocess
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
