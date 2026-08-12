#!/usr/bin/env python3
'''
Build a stand-alone Velociraptor offline collector executable.

Bundles Windows.Triage.Targets (_KapeTriage), plus Windows.System.Services,
Windows.Network.NetstatEnriched and Windows.System.DNSCache, into a
self-contained Windows .exe that zips, encrypts and uploads its results
to Azure Blob Storage via a SAS URL.

Run with: python3 scripts/standalone/build_collector.py

Requirements:
    - pip install -e . (install cycas package)
    - A Velociraptor binary reachable at $VELOCIRAPTOR_BINARY
      (downloaded automatically from $VELOCIRAPTOR_URL if missing)
    - az login, if you want to reuse the SAS URL / ZIP password
      easy_install.py stored in Key Vault during provisioning
'''

import io
import os
import secrets
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

from core.manager.keyvault import KeyvaultManager
from core.utils.postprocess import download_velociraptor

BASE_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent

load_dotenv(PROJECT_ROOT / '.env')

TRIAGE_TARGETS_URL = 'https://triage.velocidex.com/artifacts/Windows.Triage.Targets.zip'

# Secret names easy_install.py uses when it provisions an offline collector -
# reused here so a previously generated SAS URL / password can be found again.
KEYVAULT_SAS_LOCATION      = 'collector-sas-url'
KEYVAULT_PASSWORD_LOCATION = os.getenv('KEYVAULT_PASSWORDLOCATION', 'velociraptor-collection-password')

BUILD_DIR = PROJECT_ROOT / 'build' / 'collector'
DATASTORE = BUILD_DIR / 'datastore'
SPEC_PATH = BUILD_DIR / 'spec.yaml'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def step(msg):    print(f'  >> {msg}')
def info(msg):    print(f'     {msg}')
def success(msg): print(f'  [OK] {msg}')
def error(msg):   print(f'  [ERROR] {msg}', file=sys.stderr); sys.exit(1)


def confirm(prompt, default=True):
    suffix = ' [Y/n] ' if default else ' [y/N] '
    answer = input(prompt + suffix).strip().lower()
    return answer.startswith('y') if answer else default


def open_keyvault():
    '''Return an authenticated KeyvaultManager, or None if Key Vault isn't
    configured/reachable.'''
    if os.getenv('KEYVAULT_ENABLED', 'false').lower() != 'true':
        return None
    vault_url = os.getenv('KEYVAULT_URL')
    if not vault_url:
        return None
    kv = KeyvaultManager(vault_url)
    return kv if kv.authenticate(DefaultAzureCredential(), verify_enabled=False) else None


def get_sas_url(kv):
    '''Ask for the Azure Blob SAS URL used to upload results, offering to reuse
    the one easy_install.py stores in Key Vault when it provisions the input
    container.'''
    if kv:
        existing = kv.read_creds(KEYVAULT_SAS_LOCATION)
        if existing:
            preview = existing if len(existing) <= 70 else existing[:70] + '...'
            info(f"Found a SAS URL in Key Vault secret '{KEYVAULT_SAS_LOCATION}':")
            info(f'  {preview}')
            if confirm('Use this SAS URL?'):
                return existing

    sas_url = input('  Azure Blob Storage SAS URL (Write + Create permissions): ').strip()
    while not sas_url:
        sas_url = input('  SAS URL is required: ').strip()
    return sas_url


def get_zip_password(kv):
    '''Ask for the password used to encrypt the collector's output zip, offering
    to reuse the one saved in Key Vault, or generate a fresh random one.'''
    if kv:
        existing = kv.read_creds(KEYVAULT_PASSWORD_LOCATION)
        if existing:
            info(f"Found a ZIP password in Key Vault secret '{KEYVAULT_PASSWORD_LOCATION}'.")
            if confirm('Use this password?'):
                return existing

    password = input('  ZIP password (leave blank to generate one): ').strip()
    if not password:
        password = secrets.token_urlsafe(18)
        info(f'Generated password: {password}')
        info('Store this somewhere safe - it is required to open the results.')
    return password


def download_triage_targets(dest_dir):
    '''Download and extract the latest Windows.Triage.Targets artifact definition.'''
    with urlopen(TRIAGE_TARGETS_URL) as resp:
        data = resp.read()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_dir)


def write_collector_spec(spec_path, sas_url, password):
    '''Write a velociraptor `collector` spec that runs Windows.Triage.Targets
    (_KapeTriage), plus a handful of standalone artifacts, and uploads the
    encrypted result via SAS URL.'''
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(f'''OS: Windows

Artifacts:
  Windows.Triage.Targets:
    HighLevelTargets: '["_KapeTriage"]'
  Windows.System.Services:
    Calculate_hashes: "Y"
    CertificateInfo: "Y"
    DISABLE_DANGEROUS_API_CALLS: "Y"
  Windows.Network.NetstatEnriched:
    DISABLE_DANGEROUS_API_CALLS: "Y"
  Windows.System.DNSCache: {{}}

Target: Azure
TargetArgs:
  sas_url: "{sas_url}"

EncryptionScheme: Password
EncryptionArgs:
  password: "{password}"

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print('\n' + '=' * 60)
    print('  Build Offline Collector')
    print('=' * 60 + '\n')

    kv = open_keyvault()
    if kv:
        success('Authenticated with Key Vault - will offer to reuse saved secrets.')
    else:
        info('Key Vault not configured/reachable - falling back to manual input.')

    step('Resolving SAS URL...')
    sas_url = get_sas_url(kv)
    success('SAS URL set.')

    step('Resolving ZIP password...')
    password = get_zip_password(kv)
    success('ZIP password set.')

    step('Downloading latest Windows.Triage.Targets artifact definition...')
    try:
        download_triage_targets(DATASTORE / 'artifact_definitions' / 'Windows' / 'Triage')
    except Exception as e:
        error(f'Could not download Windows.Triage.Targets: {e}')
    success('Artifact definition downloaded.')

    step('Ensuring Velociraptor binary is available...')
    velo_binary = os.getenv('VELOCIRAPTOR_BINARY', '/tmp/velociraptor')
    velo_url    = os.getenv('VELOCIRAPTOR_URL', '')
    download_velociraptor(velo_binary, velo_url)
    if not os.path.exists(velo_binary):
        error(f'Velociraptor binary not available at {velo_binary}.')
    success(f'Using {velo_binary}.')

    step('Writing collector spec...')
    write_collector_spec(SPEC_PATH, sas_url, password)
    success(f'Spec written to {SPEC_PATH}.')

    step('Building offline collector...')
    result = subprocess.run(
        [velo_binary, 'collector', '--datastore', str(DATASTORE), str(SPEC_PATH)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        error((result.stderr.strip().splitlines() or ['Unknown error.'])[-1])

    collector = next(DATASTORE.glob('Collector_*'), None)
    if not collector:
        error('Collector build did not produce an output binary.')

    success(f'Collector built: {collector}')
    info('Run it on a Windows endpoint to collect Windows.Triage.Targets and')
    info('upload the encrypted results via the SAS URL above.')


if __name__ == '__main__':
    main()
