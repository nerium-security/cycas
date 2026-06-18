#!/usr/bin/env python3
'''
Cycas easy-install script.

Provisions Azure infrastructure and generates the .env configuration file.
Run with: python install.py

Requirements:
    - pip install -e . (install cycas package)
    - az login (Azure CLI, used for authentication)
    - Contributor access on the target resource group
'''

import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import random
import string
import zipfile
from urllib.request import urlopen
from pathlib import Path

from core.manager.azure import AzureManager
from core.manager.adx import AdxManager
from core.manager.blob import BlobManager
from core.manager.table import TablestorageManager
from core.manager.queue import QueueManager
from core.manager.functions import FunctionsManager
from core.manager.keyvault import KeyvaultManager, ROLE_SECRETS_OFFICER
from core.manager.webapp import WebappManager
from core.utils.postprocess import download_velociraptor

logging.basicConfig(level=logging.WARNING, format='%(message)s')

ROOT        = Path(__file__).parent
ENV_FILE    = ROOT / '.env'
ENV_EXAMPLE = ROOT / '.env_example'
STATE_FILE  = ROOT / '.install_state.json'

TRIAGE_TARGETS_URL  = 'https://triage.velocidex.com/artifacts/Windows.Triage.Targets.zip'
COLLECTOR_SAS_VALID_DAYS = 90
BINARIES_CONTAINER  = 'binaries'
KEYVAULT_PASSWORD_LOCATION = 'velociraptor-collection-password'
KEYVAULT_SAS_LOCATION      = 'collector-sas-url'

SKUS = {
    '1': ('Dev(No SLA)_Standard_E2a_v4', 'Basic',    'Small engagements / Testing  (2 vCores,  16 GB RAM/node) ~$2.40/day when idle'),
    '2': ('Standard_E8ads_v5',           'Standard', 'Large engagements            (8 vCores,  64 GB RAM/node) ~$19.00/day per node when idle'),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section(title): print(f'\n{"="*60}\n  {title}\n{"="*60}\n')
def step(msg):      print(f'  >> {msg}')
def info(msg):      print(f'     {msg}')
def success(msg):   print(f'  [OK] {msg}')
def error(msg):     print(f'  [ERROR] {msg}', file=sys.stderr); sys.exit(1)
def rand6():        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _get_wsl_ip():
    try:
        with open('/proc/version') as f:
            if 'microsoft' not in f.read().lower():
                return None
    except OSError:
        return None
    try:
        out = subprocess.check_output(['ip', 'addr', 'show', 'eth0'], text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith('inet '):
                return line.split()[1].split('/')[0]
    except Exception:
        pass
    return None


def prompt(label, default=None):
    suffix = f' [{default}]' if default is not None else ''
    while True:
        value = input(f'{label}{suffix}: ').strip()
        if value:
            return value
        if default is not None:
            return default
        print('  This field is required.')


def collect_run_mode():
    print('  How do you want to run the Cycas pipeline?')
    print()
    print('  1) Azure Functions  — automated, runs fully in the cloud. Recommended for large engagements. [recommended]')
    print('  2) Local            — manual, runs partially in the cloud (uses blob storage and Azure Data Explorer). Only for small engagements.')
    print()
    while True:
        choice = input('Select deployment mode [1]: ').strip()
        if not choice or choice == '1':
            return 'azurefunction'
        if choice == '2':
            return 'local'
        print('  Enter 1 or 2.')


def read_env_defaults(env_file):
    '''Parse a .env file into a plain dict without requiring all fields.

    First occurrence of each key wins, so prepended provisioned values take
    precedence over placeholder values from .env_example.
    '''
    defaults = {}
    try:
        for line in Path(env_file).read_text().splitlines():
            line = line.split('#')[0].strip()
            if '=' in line:
                key, _, value = line.partition('=')
                k = key.strip().lower()
                if k not in defaults:
                    defaults[k] = value.strip().strip("'\"")
    except FileNotFoundError:
        pass
    return defaults


def select_location(azure, subscription_id, default='westeurope'):
    step('Fetching available Azure regions...')
    locations = azure.list_locations(subscription_id)

    page_size = 20
    offset = 0

    while True:
        page = locations[offset:offset + page_size]
        print()
        for i, loc in enumerate(page, offset + 1):
            marker = ' *' if loc['name'] == default else ''
            print(f'  {i:>3}) {loc["name"]:<30} {loc["display_name"]}{marker}')

        has_more = offset + page_size < len(locations)
        print()
        if has_more:
            print(f'       M) Show more   (showing {offset + 1}–{offset + len(page)} of {len(locations)})')
        print(f'       Press Enter to use default [{default}]')
        print()

        choice = input('Select region: ').strip().lower()
        if not choice:
            return default
        if choice == 'm' and has_more:
            offset += page_size
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(locations):
                return locations[idx]['name']
        except ValueError:
            pass
        print('  Enter a number, M for more, or press Enter for default.')


def write_env_values(values):
    if not ENV_FILE.exists():
        ENV_FILE.write_text(ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else '')
        info('Created .env from .env_example' if ENV_EXAMPLE.exists() else 'Created empty .env')

    content = ENV_FILE.read_text()

    for key, value in values.items():
        new_line = f'{key}={value}'
        content, count = re.subn(
            rf'^{re.escape(key)}=.*$', new_line, content, flags=re.MULTILINE
        )
        if count == 0:
            content = content.rstrip('\n') + f'\n{new_line}\n'

    ENV_FILE.write_text(content)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text())
        base_required = {
            'run_mode', 'subscription_id', 'resource_group', 'location', 'new_rg',
            'cluster_name', 'database_name', 'sku_name', 'sku_tier',
            'admin_users', 'account_name', 'table_name', 'queue_name',
            'container', 'status_container', 'input_sources',
        }
        azure_required = {
            'watcher_app', 'watcher_sa', 'processor_app', 'processor_sa',
            'keyvault_name', 'keyvault_password_location', 'insights_name',
            'webapp_app', 'webapp_allowed_ips',
        }
        run_mode = data.get('run_mode', 'azurefunction')
        required = base_required | (azure_required if run_mode == 'azurefunction' else set())
        return data if required.issubset(data) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def clear_state():
    STATE_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phase 1: Collect all inputs
# ---------------------------------------------------------------------------

def collect_resource_group(azure, subscription_id, step_label='2/7'):
    section(f'Step {step_label} — Resource Group')

    step('Fetching resource groups...')
    rgs = azure.list_resource_groups(subscription_id)

    print('Available resource groups:')
    for i, rg in enumerate(rgs, 1):
        print(f'  {i}) {rg["name"]} ({rg["location"]})')
    print('  N) Create a new resource group')
    print()

    while True:
        choice = input('Select resource group: ').strip().lower()
        if choice == 'n':
            name     = prompt('New resource group name')
            location = select_location(azure, subscription_id)
            return name, location, True   # True = needs creating
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(rgs):
                rg = rgs[idx]
                return rg['name'], rg['location'], False
        except ValueError:
            pass
        print(f'  Enter a number between 1 and {len(rgs)}, or N to create a new one.')


def collect_adx_config(defaults, resource_group, step_label='3/7'):
    section(f'Step {step_label} — Azure Data Explorer')

    rg_slug = re.sub(r'[^a-z0-9]', '', resource_group.lower())
    default_cluster = (rg_slug[:13] + 'adx' + rand6())  # max 22 chars, no hyphens allowed
    while True:
        cluster_name = prompt('Cluster name (4-22 lowercase letters and numbers, no hyphens)', default=default_cluster)
        if re.fullmatch(r'[a-z][a-z0-9]{3,21}', cluster_name):
            break
        print('  Invalid name: must be 4-22 characters, start with a letter, only lowercase letters and numbers.')
    database_name = prompt('Database name', default=defaults.get('adx_database_name', 'dfir'))

    print('\n  Cluster size:')
    for key, (sku, _, desc) in SKUS.items():
        print(f'  {key}) {sku:<35} {desc}')
    while True:
        sku_choice = prompt('Select cluster size', default='1')
        if sku_choice in SKUS:
            break
        print(f'  Enter a number between 1 and {len(SKUS)}.')
    sku_name, sku_tier, _ = SKUS[sku_choice]

    return cluster_name, database_name, sku_name, sku_tier


def collect_admin_users(adx):
    admins = []

    while True:
        print()
        query = input('  Add cluster admin by searching for user by name or email (or press Enter to finish): ').strip()
        if not query:
            break

        try:
            users = adx.search_users(query)
        except Exception as e:
            info(f'Search failed: {e}')
            continue

        if not users:
            info('No users found.')
            continue

        print()
        for i, u in enumerate(users, 1):
            print(f'  {i:>2}) {u["displayName"]:<35} {u["userPrincipalName"]}')
        print()

        choice = input('  Select user [number, or Enter to search again]: ').strip()
        if not choice:
            continue

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(users):
                admins.append(users[idx])
                success(f"'{users[idx]['displayName']}' added to admin list.")
        except (ValueError, IndexError):
            info('Invalid selection.')

    return admins


def collect_input_sources(defaults, account_name, container, step_label='5/7'):
    section(f'Step {step_label} — Input Sources')

    info('Select which input source(s) to use for triage packages.')
    info('You can enable multiple sources.')
    print()
    print('  1) Blob  — Azure Blob Storage (managed identity, using the storage account provisioned above)  [recommended]')
    print('  2) SAS   — Azure Blob Storage via Shared Access Signature (SAS) token')
    print('  3) SFTP  — Remote file server via SSH')
    print()
    info('Enter one or more numbers separated by spaces (e.g. "1 3" for Blob + SFTP).')
    print()

    while True:
        raw = input('Select input source(s) [1]: ').strip()
        if not raw:
            raw = '1'
        choices = raw.split()
        if choices and all(c in {'1', '2', '3'} for c in choices):
            break
        print('  Enter one or more of: 1 (Blob), 2 (SAS), 3 (SFTP).')

    sources = {}

    if '1' in choices:
        blob_uri = f'https://{account_name}.blob.core.windows.net'
        sources['blob'] = {
            'BLOB_STORAGEACCOUNT_ENABLED': 'true',
            'BLOB_STORAGEACCOUNT_URI':     blob_uri,
            'BLOB_CONTAINER_INPUT':        container,
        }

    if '2' in choices:
        print()
        info('Paste the full SAS URL (includes container path and token query string).')
        sas_url = prompt('SAS URL', default=defaults.get('blob_storageaccount_sas', ''))
        sources['sas'] = {
            'BLOB_STORAGEACCOUNT_SAS_ENABLED': 'true',
            'BLOB_STORAGEACCOUNT_SAS':          sas_url,
        }

    if '3' in choices:
        print()
        info('The SFTP private key is fetched from Key Vault at runtime.')
        sftp_url  = prompt('SFTP hostname',                              default=defaults.get('sftp_url', ''))
        sftp_user = prompt('SFTP username',                              default=defaults.get('sftp_username', ''))
        sftp_port = prompt('SFTP port',                                  default=defaults.get('sftp_port', '22'))
        sftp_kv   = prompt('Key Vault secret name for SFTP private key', default=defaults.get('sftp_keyvaultsecretname', 'sftp-key'))
        sources['sftp'] = {
            'SFTP_ENABLED':            'true',
            'SFTP_URL':                sftp_url,
            'SFTP_USERNAME':           sftp_user,
            'SFTP_PORT':               sftp_port,
            'SFTP_KEYVAULTSECRETNAME': sftp_kv,
        }

    return sources


def collect_functions_config(resource_group, step_label='6/7'):
    section(f'Step {step_label} — Azure Functions')

    info('Each function app gets its own dedicated storage account (Azure best practice).')
    print()

    watcher_app   = prompt('Watcher function app name',   default=f'{resource_group[:43]}-watcher-{rand6()}')
    watcher_clean = re.sub(r'[^a-z0-9]', '', watcher_app.lower())
    watcher_sa    = prompt('Watcher storage account name (3-24 alphanumeric)',
                           default=watcher_clean[:18] + rand6())

    processor_app = prompt('Processor function app name', default=f'{resource_group[:41]}-processor-{rand6()}')
    processor_clean = re.sub(r'[^a-z0-9]', '', processor_app.lower())
    processor_sa  = prompt('Processor storage account name (3-24 alphanumeric)',
                           default=processor_clean[:18] + rand6())

    rg_slug       = re.sub(r'[^a-z0-9]', '', resource_group.lower())
    insights_name = prompt('Application Insights name', default=f'{rg_slug[:50]}-insights')

    return watcher_app, watcher_sa, processor_app, processor_sa, insights_name


def collect_keyvault_config(resource_group, step_label='7/7'):
    section(f'Step {step_label} — Key Vault (optional)')

    info('A Key Vault is only required if triage packages collected with Velociraptor')
    info('are protected with a ZIP password. You can skip this step if packages are')
    info('not password-protected.')
    print()

    create_kv = input('  Create a Key Vault for ZIP password storage? [Y/n]: ').strip().lower()
    if create_kv in ('n', 'no'):
        return None, None, None

    import getpass
    rg_slug   = re.sub(r'[^a-z0-9-]', '-', resource_group.lower()).strip('-')
    kv_suffix = f'-{rand6()}-keyvault'
    kv_default = rg_slug[:24 - len(kv_suffix)] + kv_suffix
    keyvault_name     = prompt('Key Vault name', default=kv_default)
    password_location = prompt('Location for the ZIP password in Key Vault', default=KEYVAULT_PASSWORD_LOCATION)
    zip_password      = getpass.getpass('  ZIP password to store in Key Vault: ')

    return keyvault_name, password_location, zip_password


def collect_webapp_config(resource_group, step_label='8/8'):
    section(f'Step {step_label} — Web Application')

    info('A Web App provides a status dashboard for the Cycas pipeline.')
    print()
    create_wa = input('  Set up the Web Application? [Y/n]: ').strip().lower()
    if create_wa in ('n', 'no'):
        return None, None, None

    print()
    info('Run the webapp locally (1) or deploy it to Azure App Service (2)?')
    info('  1) Local  — Fastest and most secure option. Only you can access it on your device.')
    info('  2) Azure  — Adds 10+ minutes of deployment time. Multiple teammembers can access it.')
    print()
    mode_choice = input('  Select [1]: ').strip()
    if mode_choice == '2':
        webapp_mode = 'azure'
    else:
        webapp_mode = 'local'

    if webapp_mode == 'local':
        return 'local', None, None

    rg_slug  = re.sub(r'[^a-z0-9-]', '-', resource_group.lower()).strip('-')
    app_name = prompt('Web app name', default=f'{rg_slug[:43]}-webapp-{rand6()}')

    print()
    print('  Enter IP addresses or CIDR ranges allowed to access the webapp.')
    print('  All other traffic will be denied.')
    print()

    try:
        my_ip = urlopen('https://api.ipify.org', timeout=5).read().decode()
    except Exception:
        my_ip = None

    allowed_ips = []
    if my_ip:
        answer = input(f'  Add your current external IP ({my_ip})? [Y/n]: ').strip().lower()
        if answer not in ('n', 'no'):
            allowed_ips.append(my_ip)
            success(f"'{my_ip}' added.")
        print()

    while True:
        ip = input('  Add IP / CIDR (or press Enter to finish): ').strip()
        if not ip:
            if not allowed_ips:
                print('  At least one IP address is required.')
                continue
            break
        allowed_ips.append(ip)
        success(f"'{ip}' added.")

    return webapp_mode, app_name, allowed_ips


def collect_setup_mode():
    section('Setup Mode')
    print('  1) Easy     - sensible defaults [recommended]')
    print('  2) Advanced - Allows you to configure more granual settings, like names for Azure resources.')
    print()
    while True:
        choice = input('  Setup mode [1]: ').strip() or '1'
        if choice in ('1', '2'):
            return 'easy' if choice == '1' else 'advanced'
        print('  Enter 1 or 2.')


def collect_adx_sku(step_label='3'):
    section(f'Step {step_label} — Cluster Size')
    for key, (sku, _, desc) in SKUS.items():
        print(f'  {key}) {sku:<35} {desc}')
    print()
    while True:
        sku_choice = prompt('Select cluster size', default='1')
        if sku_choice in SKUS:
            break
        print(f'  Enter a number between 1 and {len(SKUS)}.')
    sku_name, sku_tier, _ = SKUS[sku_choice]
    return sku_name, sku_tier


def auto_generate_names(resource_group, defaults):
    '''Return all technical resource names derived from the resource group, using a shared random suffix.'''
    suffix        = rand6()
    rg_slug       = re.sub(r'[^a-z0-9]', '', resource_group.lower())

    account_name     = (rg_slug + 'data')[:18] + suffix
    table_name       = defaults.get('blob_logtable_name',    'statusupdate')
    queue_name       = defaults.get('blob_queue_name',       'triagepackages')
    container        = defaults.get('blob_container_input',  'uploads')
    status_container = defaults.get('blob_container_status', 'status')

    cluster_name  = (rg_slug[:13] + 'adx' + suffix)
    database_name = defaults.get('adx_database_name', 'dfir')

    watcher_app   = f'{resource_group[:43]}-watcher-{suffix}'
    watcher_sa    = (rg_slug + 'watcher')[:18] + suffix
    processor_app = f'{resource_group[:41]}-processor-{suffix}'
    processor_sa  = (rg_slug + 'processor')[:18] + suffix
    insights_name = f'{rg_slug[:50]}-insights'
    keyvault_name = f'{rg_slug[:14]}-kv-{suffix}'

    return (account_name, table_name, queue_name, container, status_container,
            cluster_name, database_name, watcher_app, watcher_sa, processor_app, processor_sa, insights_name, keyvault_name)


def collect_storage_config(defaults, resource_group, step_label='4/7'):
    section(f'Step {step_label} — Storage Account')

    rg_slug = re.sub(r'[^a-z0-9]', '', resource_group.lower())
    account_name     = prompt('Storage account name (3-24 lowercase alphanumeric)', default=(rg_slug + 'data')[:18] + rand6())
    table_name       = prompt('Status table name',              default=defaults.get('blob_logtable_name',    'statusupdate'))
    queue_name       = prompt('Queue name',                     default=defaults.get('blob_queue_name',       'triagepackages'))
    container        = prompt('Blob container for uploads',     default=defaults.get('blob_container_input',  'uploads'))
    status_container = prompt('Blob container for status JSON', default=defaults.get('blob_container_status', 'status'))
    config_container = prompt('Blob container for config',      default=defaults.get('blob_container_config', 'config'))

    return account_name, table_name, queue_name, container, status_container, config_container


# ---------------------------------------------------------------------------
# Phase 2: Confirm
# ---------------------------------------------------------------------------

def confirm_plan(run_mode,
                 resource_group, location, new_rg,
                 cluster_name, database_name, sku_name,
                 admin_users,
                 account_name, table_name, queue_name, container, status_container, config_container,
                 input_sources,
                 watcher_app=None, watcher_sa=None,
                 processor_app=None, processor_sa=None,
                 keyvault_name=None, keyvault_password_location=None,
                 webapp_mode=None, webapp_app=None, webapp_allowed_ips=None):

    section('Summary — Review before provisioning')

    mode_label = 'Azure Functions (cloud)' if run_mode == 'azurefunction' else 'Local (manual)'
    info(f'Deployment mode: {mode_label}')

    print('\n  Resource group')
    info(f'Name     : {resource_group}  {"(will be created)" if new_rg else "(existing)"}')
    info(f'Location : {location}')

    print('\n  Azure Data Explorer')
    info(f'Cluster  : {cluster_name}')
    info(f'Database : {database_name}')
    info(f'SKU      : {sku_name}')
    if admin_users:
        info(f'Admins   : {", ".join(u["displayName"] for u in admin_users)}')

    print('\n  Storage Account (data)')
    info(f'Account         : {account_name}')
    info(f'Table           : {table_name}')
    info(f'Queue           : {queue_name}')
    info(f'Container       : {container}')
    info(f'Status container: {status_container}')
    info(f'Config container: {config_container}')

    print('\n  Input Sources')
    if 'blob' in input_sources:
        info(f'Blob : enabled  ({account_name} / {container})')
    if 'sas' in input_sources:
        info(f'SAS  : enabled')
    if 'sftp' in input_sources:
        sftp = input_sources['sftp']
        info(f'SFTP : enabled  ({sftp["SFTP_USERNAME"]}@{sftp["SFTP_URL"]}:{sftp["SFTP_PORT"]})')

    if run_mode == 'azurefunction':
        print('\n  Azure Functions')
        info(f'Watcher  : {watcher_app}  (storage: {watcher_sa})')
        info(f'Processor: {processor_app}  (storage: {processor_sa})')
        if keyvault_name:
            info(f'Key Vault: {keyvault_name}  (secret: {keyvault_password_location})')
        if webapp_mode == 'local':
            print('\n  Web Application')
            info('Mode: Local (storage roles assigned to current user)')
        elif webapp_mode == 'azure' and webapp_app:
            print('\n  Web Application')
            info(f'Mode       : Azure App Service')
            info(f'App name   : {webapp_app}')
            info(f'Allowed IPs: {", ".join(webapp_allowed_ips)}')

    print()
    confirm = input('Proceed with provisioning? [Y/n]: ').strip().lower()
    return confirm in ('', 'y', 'yes')


# ---------------------------------------------------------------------------
# Phase 2b: Key Vault provisioning (optional)
# ---------------------------------------------------------------------------

def provision_keyvault(credential, subscription_id, resource_group, location,
                       vault_name, password_location, zip_password,
                       principal_id):

    section('Provisioning — Key Vault')

    step(f"Creating Key Vault '{vault_name}'...")
    vault_uri = KeyvaultManager.provision_vault(
        credential, subscription_id, resource_group, vault_name, location
    )
    success(f"Key Vault ready: {vault_uri}")

    step(f"Granting current user Secrets Officer on '{vault_name}'...")
    KeyvaultManager.assign_role(
        credential, subscription_id, resource_group, vault_name,
        principal_id, ROLE_SECRETS_OFFICER
    )
    success('Secrets Officer role assigned.')

    if zip_password is not None:
        step(f"Uploading ZIP password as secret '{password_location}'...")
        kv = KeyvaultManager(vault_uri)
        kv.authenticate(credential, verify_enabled=False)
        kv.set_secret(password_location, zip_password)
        success(f"Secret '{password_location}' uploaded.")
    else:
        info(f"Skipping secret upload - set '{password_location}' in Key Vault manually if you use a password for the offline collector ZIP.")

    portal_url = (
        f'https://portal.azure.com/#resource/subscriptions/{subscription_id}'
        f'/resourceGroups/{resource_group}'
        f'/providers/Microsoft.KeyVault/vaults/{vault_name}'
    )
    write_env_values({
        'KEYVAULT_ENABLED':          'true',
        'KEYVAULT_URL':              vault_uri,
        'KEYVAULT_PORTAL_URL':       portal_url,
        'KEYVAULT_PASSWORDLOCATION': password_location,
    })
    success('.env updated with Key Vault settings.')


# ---------------------------------------------------------------------------
# Phase 3: Provision
# ---------------------------------------------------------------------------

def provision_storage(azure, credential, subscription_id,
                      resource_group, location, new_rg,
                      account_name, table_name, queue_name, container, status_container,
                      config_container,
                      defaults):

    section('Provisioning — Storage')

    if new_rg:
        step(f"Creating resource group '{resource_group}'...")
        azure.create_resource_group(subscription_id, resource_group, location)
        success(f"Resource group '{resource_group}' created.")

    blob_uri       = f'https://{account_name}.blob.core.windows.net'
    table_endpoint = f'https://{account_name}.table.core.windows.net'
    queue_url      = f'https://{account_name}.queue.core.windows.net'

    step(f"Ensuring storage account '{account_name}' exists...")
    blob_mgr = BlobManager(credential, blob_uri)
    blob_mgr.provision_storage_account(subscription_id, resource_group, location, account_name)
    success(f"Storage account '{account_name}' ready.")

    step(f"Ensuring table '{table_name}' exists...")
    TablestorageManager(credential, table_endpoint, table_name).authenticate(verify_enabled=False)
    success(f"Table '{table_name}' ready.")

    step(f"Ensuring queue '{queue_name}' exists...")
    QueueManager(credential, queue_url, queue_name).authenticate(verify_enabled=False)
    success(f"Queue '{queue_name}' ready.")

    step(f"Ensuring blob container '{container}' exists...")
    blob_mgr.authenticate()
    blob_mgr.switch_to_account_key(subscription_id, resource_group, account_name)
    blob_mgr.create_container(container)
    success(f"Container '{container}' ready.")

    step(f"Ensuring blob container '{status_container}' exists...")
    blob_mgr.create_container(status_container)
    success(f"Container '{status_container}' ready.")

    step(f"Ensuring blob container '{config_container}' exists...")
    blob_mgr.create_container(config_container)
    success(f"Container '{config_container}' ready.")

    step(f"Ensuring blob container '{BINARIES_CONTAINER}' exists...")
    blob_mgr.create_container(BINARIES_CONTAINER)
    success(f"Container '{BINARIES_CONTAINER}' ready.")

    step(f"Uploading initial velociraptor_artifacts.json to '{config_container}' container...")
    import json as _json
    artifacts_src = Path(__file__).parent / 'velociraptor' / 'artifacts' / 'velociraptor_artifacts.json'
    if artifacts_src.exists():
        blob_mgr.upload_json(config_container, 'velociraptor_artifacts.json', _json.loads(artifacts_src.read_text()))
        success('velociraptor_artifacts.json uploaded.')
    else:
        info('velociraptor_artifacts.json not found locally, skipping upload.')

    step('Exporting artifact definitions from Velociraptor binary...')
    if artifacts_src.exists():
        env_vals       = read_env_defaults(ENV_FILE)
        binary         = env_vals.get('velociraptor_binary', '/tmp/velociraptor')
        defs_dest      = Path(env_vals.get('velociraptor_definitions', 'velociraptor/definitions/'))
        artifacts_data = json.loads(artifacts_src.read_text())
        all_names      = [
            entry.split('(')[0].strip()
            for entries in artifacts_data.values()
            for entry in entries
        ]
        defs_dest.mkdir(parents=True, exist_ok=True)
        exported = 0
        for name in all_names:
            dest = defs_dest / f'{name}.yaml'
            if dest.exists():
                continue
            result = subprocess.run(
                [binary, 'artifacts', 'show', name],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                dest.write_text(result.stdout)
                exported += 1
            else:
                info(f'Could not export {name}: {result.stderr.strip()}')
        success(f'{exported} artifact definition(s) exported to {defs_dest}.')
    else:
        info('velociraptor_artifacts.json not found, skipping definition export.')

    step(f"Uploading Velociraptor definition YAMLs to '{config_container}/definitions/' ...")
    if defs_dest.is_dir():
        yaml_files = list(defs_dest.glob('*.yaml')) + list(defs_dest.glob('*.yml'))
        for yf in yaml_files:
            blob_mgr.upload_text(config_container, f'definitions/{yf.name}', yf.read_text())
        success(f'{len(yaml_files)} definition YAML(s) uploaded.')
    else:
        info('Definitions folder not found, skipping YAML upload.')

    step('Writing .env...')

    write_env_values({
        'BLOB_LOGTABLE_ENABLED':   'true',
        'BLOB_LOGTABLE_URI':       table_endpoint,
        'BLOB_LOGTABLE_NAME':      table_name,
        'BLOB_QUEUE_ENABLED':      'true',
        'BLOB_QUEUE_URL':          queue_url,
        'BLOB_QUEUE_NAME':         queue_name,
        'BLOB_CONTAINER_STATUS':   status_container,
        'BLOB_CONTAINER_CONFIG':   config_container,
    })

    success('.env updated.')


def provision_adx(credential, subscription_id,
                  resource_group, location,
                  cluster_name, database_name, sku_name, sku_tier,
                  admin_users,
                  defaults):

    section('Provisioning — Azure Data Explorer')

    adx = AdxManager(credential, adx_cluster_uri='', adx_cluster_ingestion_uri='', adx_database_name=database_name)

    step('Ensuring ADX cluster exists (may take 10+ minutes if new)...')
    cluster = adx.provision_cluster(subscription_id, resource_group, location, cluster_name, sku_name, sku_tier)
    success(f'Cluster ready: {cluster.uri}')

    step(f"Ensuring database '{database_name}' exists...")
    adx.provision_database(resource_group, cluster_name, database_name, location)
    success(f"Database '{database_name}' ready.")

    adx.adx_cluster_uri           = cluster.uri
    adx.adx_cluster_ingestion_uri = cluster.data_ingestion_uri
    adx.authenticate(verify_enabled=False)

    step('Ensuring current user has AllDatabasesAdmin...')
    principal_id, principal_type = adx.get_current_user_id()
    adx.AllDatabasesAdmin(resource_group, cluster_name, principal_id, principal_type)
    success('AllDatabasesAdmin ready for current user.')

    for user in admin_users:
        step(f"Ensuring AllDatabasesAdmin for '{user['displayName']}'...")
        assignment_name = f'cycas-admin-{user["id"][:8]}'
        adx.assign_cluster_admin(resource_group, cluster_name, user['id'], 'User', assignment_name)
        success(f"'{user['displayName']}' has AllDatabasesAdmin.")

    step(f"Setting ingestion batching policy on '{database_name}'...")
    adx.set_ingestion_batching_policy(
        database_name,
        max_time=defaults.get('adx_ingestion_batching_timespan', '00:00:30'),
        max_items=int(defaults.get('adx_ingestion_batching_max_items', 2500)),
        max_size_mb=int(defaults.get('adx_ingestion_batching_max_size_mb', 4096)),
    )
    success('Ingestion batching policy set.')

    step('Writing .env...')

    write_env_values({
        'ADX_CLUSTER_ENABLED':                'true',
        'ADX_CLUSTER_URI':                    cluster.uri,
        'ADX_CLUSTER_INGESTION_URI':          cluster.data_ingestion_uri,
        'ADX_DATABASE_NAME':                  database_name,
        'ADX_TABLE_PREFIX':                   defaults.get('adx_table_prefix', 'cycas_'),
        'ADX_INGESTION_BATCHING_TIMESPAN':    defaults.get('adx_ingestion_batching_timespan', '00:00:30'),
        'ADX_INGESTION_BATCHING_MAX_ITEMS':   defaults.get('adx_ingestion_batching_max_items', '2500'),
        'ADX_INGESTION_BATCHING_MAX_SIZE_MB': defaults.get('adx_ingestion_batching_max_size_mb', '4096'),
    })

    success('.env updated.')


# ---------------------------------------------------------------------------
# Phase 4: Azure Functions
# ---------------------------------------------------------------------------

def provision_functions(credential, subscription_id,
                        resource_group, location,
                        data_account,
                        cluster_name,
                        watcher_app, watcher_sa,
                        processor_app, processor_sa,
                        keyvault_name, insights_name):

    section('Provisioning — Azure Functions')

    funcs = FunctionsManager(credential, subscription_id)

    step(f"Ensuring Application Insights '{insights_name}' exists...")
    ai_conn_str = funcs.provision_app_insights(resource_group, insights_name, location)
    success(f"Application Insights '{insights_name}' ready.")

    # Dedicated storage accounts (one per function app — Azure best practice)
    step(f"Ensuring watcher storage account '{watcher_sa}' exists...")
    BlobManager(credential, f'https://{watcher_sa}.blob.core.windows.net').provision_storage_account(
        subscription_id, resource_group, location, watcher_sa
    )
    success(f"'{watcher_sa}' ready.")

    step(f"Ensuring processor storage account '{processor_sa}' exists...")
    BlobManager(credential, f'https://{processor_sa}.blob.core.windows.net').provision_storage_account(
        subscription_id, resource_group, location, processor_sa
    )
    success(f"'{processor_sa}' ready.")

    watcher_conn   = funcs.get_storage_connection_string(resource_group, watcher_sa)
    processor_conn = funcs.get_storage_connection_string(resource_group, processor_sa)

    # Deployment containers (Flex Consumption runs directly from a blob container)
    step("Ensuring deployment containers exist...")
    funcs.create_deployment_container(watcher_conn)
    funcs.create_deployment_container(processor_conn)
    success("Deployment containers ready.")

    watcher_deploy_url   = f'https://{watcher_sa}.blob.core.windows.net/deployments'
    processor_deploy_url = f'https://{processor_sa}.blob.core.windows.net/deployments'

    # Flex Consumption plans are 1-to-1 with function apps — one plan each
    watcher_plan_name   = f'{watcher_app}-plan'
    processor_plan_name = f'{processor_app}-plan'

    step(f"Ensuring App Service Plan '{watcher_plan_name}' exists...")
    watcher_plan = funcs.provision_app_service_plan(resource_group, watcher_plan_name, location)
    success(f"Plan '{watcher_plan_name}' ready.")

    step(f"Ensuring App Service Plan '{processor_plan_name}' exists...")
    processor_plan = funcs.provision_app_service_plan(resource_group, processor_plan_name, location)
    success(f"Plan '{processor_plan_name}' ready.")

    # Function apps (watcher: 512 MB — light work; processor: 2048 MB — heavy work)
    step(f"Ensuring watcher function app '{watcher_app}' exists (512 MB)...")
    watcher_result = funcs.provision_function_app(
        resource_group, watcher_app, location, watcher_plan['id'],
        instance_memory_mb=512,
        deployment_container_url=watcher_deploy_url,
    )
    success(f"'{watcher_app}' ready.")

    step(f"Ensuring processor function app '{processor_app}' exists (2048 MB)...")
    processor_result = funcs.provision_function_app(
        resource_group, processor_app, location, processor_plan['id'],
        instance_memory_mb=4096,
        deployment_container_url=processor_deploy_url,
    )
    success(f"'{processor_app}' ready.")

    watcher_principal   = watcher_result.get('identity', {}).get('principalId')
    processor_principal = processor_result.get('identity', {}).get('principalId')

    # RBAC: grant both apps access to the data storage account
    step("Ensuring data storage roles for watcher managed identity...")
    funcs.assign_data_storage_roles(resource_group, data_account, watcher_principal)
    success('Watcher storage roles ready.')

    step("Ensuring data storage roles for processor managed identity...")
    funcs.assign_data_storage_roles(resource_group, data_account, processor_principal)
    success('Processor storage roles ready.')

    step(f"Ensuring AllDatabasesAdmin for processor managed identity on '{cluster_name}'...")
    adx_tmp = AdxManager(credential, '', '', '')
    adx_tmp.assign_cluster_admin(
        resource_group, cluster_name, processor_principal, 'App',
        f'cycas-processor-{processor_principal[:8]}',
        subscription_id=subscription_id,
    )
    success('Processor ADX role ready.')

    if keyvault_name:
        step(f"Ensuring Key Vault Secrets User for watcher on '{keyvault_name}'...")
        funcs.assign_keyvault_roles(resource_group, keyvault_name, watcher_principal)
        success('Watcher Key Vault role ready.')

        step(f"Ensuring Key Vault Secrets User for processor on '{keyvault_name}'...")
        funcs.assign_keyvault_roles(resource_group, keyvault_name, processor_principal)
        success('Processor Key Vault role ready.')

    # App settings: read the final .env and convert to Azure app settings
    env_raw      = read_env_defaults(ENV_FILE)
    env_settings = {k.upper(): v for k, v in env_raw.items() if v}

    queue_service_uri = f'https://{data_account}.queue.core.windows.net'

    watcher_settings = {
        'AzureWebJobsStorage':                    watcher_conn,
        'CYCAS_DEPLOY_STORAGE':                   watcher_conn,
        'APPLICATIONINSIGHTS_CONNECTION_STRING':   ai_conn_str,
        **env_settings,
    }
    # Processor also needs the managed-identity connection for its queue trigger
    processor_settings = {
        'AzureWebJobsStorage':                    processor_conn,
        'CYCAS_DEPLOY_STORAGE':                   processor_conn,
        'CYCAS_DATASTORAGE__queueServiceUri':      queue_service_uri,
        'CYCAS_DATASTORAGE__credential':           'managedidentity',
        'APPLICATIONINSIGHTS_CONNECTION_STRING':   ai_conn_str,
        **env_settings,
    }

    step(f"Applying app settings to '{watcher_app}'...")
    funcs.set_app_settings(resource_group, watcher_app, watcher_settings)
    success('Watcher settings applied.')

    step(f"Applying app settings to '{processor_app}'...")
    funcs.set_app_settings(resource_group, processor_app, processor_settings)
    success('Processor settings applied.')

    # The Flex Consumption scale controller can keep using a stale cached
    # config for a while after the ARM update above — restart both apps to
    # force a reload, or the deploy below can fail with MissingDeploymentConfigException.
    step('Restarting function apps to refresh deployment config...')
    funcs.restart(resource_group, watcher_app)
    funcs.restart(resource_group, processor_app)
    time.sleep(30)
    success('Function apps restarted.')

    # Deploy code (ZIP includes function files + core/ package)
    root     = Path(__file__).parent
    core_dir = root / 'core'
    step(f"Deploying watcher to '{watcher_app}'...")
    info(f'Command: func azure functionapp publish {watcher_app}')
    funcs.deploy(watcher_app, root / 'azurefunctions' / 'watcher', core_dir)
    success('Watcher deployed.')

    step(f"Deploying processor to '{processor_app}'...")
    info(f'Command: func azure functionapp publish {processor_app}')
    funcs.deploy(processor_app, root / 'azurefunctions' / 'processor', core_dir)
    success('Processor deployed.')

# ---------------------------------------------------------------------------
# Phase 5: Web App
# ---------------------------------------------------------------------------

def provision_webapp_all(credential, subscription_id, resource_group, location,
                         account_name, status_container, config_container, webapp_app, allowed_ips):

    section('Provisioning — Web Application')

    webapp   = WebappManager(credential, subscription_id)
    root     = Path(__file__).parent
    core_dir = root / 'core'

    plan_name = f'{webapp_app}-plan'
    step(f"Ensuring App Service Plan '{plan_name}' exists...")
    plan = webapp.provision_plan(resource_group, plan_name, location)
    success(f"Plan '{plan_name}' ready.")

    step(f"Ensuring Web App '{webapp_app}' exists...")
    result = webapp.provision_webapp(resource_group, webapp_app, location, plan['id'])
    principal_id = result.get('identity', {}).get('principalId')
    success(f"Web App '{webapp_app}' ready.")

    step(f"Configuring IP restrictions ({len(allowed_ips)} rule(s))...")
    webapp.configure_ip_restrictions(resource_group, webapp_app, allowed_ips)
    success('IP restrictions applied.')

    step('Assigning Table Data Contributor to web app managed identity...')
    webapp.assign_storage_roles(resource_group, account_name, principal_id)
    success('Table Data Contributor assigned.')

    step(f"Assigning Blob Data Reader on '{status_container}' container to web app managed identity...")
    webapp.assign_blob_container_reader(resource_group, account_name, status_container, principal_id)
    success(f"Blob Data Reader on '{status_container}' container assigned.")

    step(f"Assigning Blob Data Contributor on '{config_container}' container to web app managed identity...")
    webapp.assign_blob_container_contributor(resource_group, account_name, config_container, principal_id)
    success(f"Blob Data Contributor on '{config_container}' container assigned.")

    step(f"Configuring startup command on '{webapp_app}'...")
    webapp.configure_startup(resource_group, webapp_app,
                             'gunicorn --bind=0.0.0.0:8000 --timeout 600 webapp.app:app')
    success('Startup command configured.')

    step(f"Applying app settings to '{webapp_app}'...")
    env_raw      = read_env_defaults(ENV_FILE)
    env_settings = {k.upper(): v for k, v in env_raw.items() if v}
    webapp.set_app_settings(resource_group, webapp_app, env_settings)
    success('App settings applied.')

    step(f"Deploying webapp code to '{webapp_app}'...")
    webapp.deploy(resource_group, webapp_app, root / 'webapp', core_dir)
    success(f"Webapp deployed.")

    success(f"Web App accessible at: https://{webapp_app}.azurewebsites.net")


def download_triage_targets(dest_dir):
    '''Download and extract the latest Windows.Triage.Targets artifact definition.'''
    with urlopen(TRIAGE_TARGETS_URL) as resp:
        data = resp.read()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest_dir)


def write_collector_spec(spec_path, sas_url, password):
    '''Write a velociraptor `collector` spec that runs Windows.Triage.Targets with
    the _KapeTriage and _Live high-level targets and uploads the encrypted
    result via SAS URL.'''
    spec_path.write_text(f'''OS: Windows

Artifacts:
  Windows.Triage.Targets:
    HighLevelTargets:
      - _KapeTriage
      - _Live

Target: Azure
TargetArgs:
  sas_url: "{sas_url}"

EncryptionScheme: Password
EncryptionArgs:
  password: "{password}"

OptVerbose: "Y"
OptBanner: "Y"
OptPrompt: "N"
OptAdmin: "Y"
OptLevel: "4"
OptConcurrency: "2"
OptFormat: "jsonl"
OptFilenameTemplate: "Triage-%FQDN%-%TIMESTAMP%"
OptProgressTimeout: "1800"
OptTimeout: "0"
OptDeleteAtExit: "Y"
''')


def provision_collector(credential, subscription_id, resource_group,
                        account_name, container,
                        keyvault_name, keyvault_password_location, zip_password,
                        defaults):
    '''Build a stand-alone Velociraptor collector that runs Windows.Triage.Targets
    with the _KapeTriage and _Live high-level targets and uploads the encrypted
    result straight to the just-provisioned input container via a write-only SAS URL.'''

    section('Provisioning — Offline Collector')

    step('Resolving ZIP password for the collector...')
    password_location = keyvault_password_location or KEYVAULT_PASSWORD_LOCATION
    if keyvault_name:
        kv = KeyvaultManager(f'https://{keyvault_name}.vault.azure.net')
        kv.authenticate(credential, verify_enabled=False)
        if zip_password:
            password = zip_password
        else:
            password = kv.read_creds(password_location)
            if password:
                info(f"Reusing existing password from Key Vault secret '{password_location}'.")
            else:
                password = secrets.token_urlsafe(18)
                kv.set_secret(password_location, password)
                info(f"Generated a new password and stored it in Key Vault secret '{password_location}'.")
    else:
        password = zip_password or secrets.token_urlsafe(18)
        info('No Key Vault configured - store this ZIP password securely:')
        info(f'  {password}')
    success('ZIP password ready.')

    step('Generating write-only SAS URL for the input container...')
    blob_uri = f'https://{account_name}.blob.core.windows.net'
    blob = BlobManager(credential, blob_uri)
    sas_url, expiry = blob.generate_container_sas_url(
        subscription_id, resource_group, account_name, container,
        expiry_days=COLLECTOR_SAS_VALID_DAYS,
    )
    success(f'SAS URL ready (expires {expiry:%Y-%m-%d}).')

    if keyvault_name:
        step(f"Uploading SAS URL to Key Vault secret '{KEYVAULT_SAS_LOCATION}'...")
        kv.set_secret(KEYVAULT_SAS_LOCATION, sas_url)
        success(f"SAS URL stored in Key Vault secret '{KEYVAULT_SAS_LOCATION}'.")

    step('Downloading latest Windows.Triage.Targets artifact definition...')
    build_dir = ROOT / 'build' / 'collector'
    datastore = build_dir / 'datastore'
    try:
        download_triage_targets(datastore / 'artifact_definitions' / 'Windows' / 'Triage')
    except Exception as e:
        info(f'Could not download Windows.Triage.Targets: {e}')
        info('Re-run easy_install.py once you have network access to build the collector.')
        return
    success('Artifact definition downloaded.')

    step('Ensuring Velociraptor binary is available...')
    velo_binary = defaults.get('velociraptor_binary', '/tmp/velociraptor')
    velo_url    = defaults.get('velociraptor_url', '')
    download_velociraptor(velo_binary, velo_url)
    if not os.path.exists(velo_binary):
        info(f'Velociraptor binary not available at {velo_binary}.')
        info('Re-run easy_install.py once it can be downloaded to build the collector.')
        return
    success(f'Using {velo_binary}.')

    step('Writing collector spec...')
    spec_path = build_dir / 'spec.yaml'
    write_collector_spec(spec_path, sas_url, password)
    success(f'Spec written to {spec_path}.')

    step('Building offline collector...')
    result = subprocess.run(
        [velo_binary, 'collector', '--datastore', str(datastore), str(spec_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        info('Collector build failed:')
        info((result.stderr.strip().splitlines() or ['Unknown error.'])[-1])
        return

    collector = next(datastore.glob('Collector_*'), None)
    if not collector:
        info('Collector build did not produce an output binary.')
        return

    success(f'Collector built: {collector}')
    info('Run it on a Windows endpoint to collect Windows.Triage.Targets and')
    info(f'uploads the encrypted result to: {blob_uri}/{container}')
    info(f'The SAS URL embedded in the collector expires {expiry:%Y-%m-%d}.')

    step(f"Uploading collector binary to '{BINARIES_CONTAINER}' container...")
    blob.authenticate()
    blob.switch_to_account_key(subscription_id, resource_group, account_name)
    blob.upload_file(BINARIES_CONTAINER, collector.name, collector)
    success(f'Collector available at: {blob_uri}/{BINARIES_CONTAINER}/{collector.name}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    section('Cycas Install')
    print('  This wizard provisions all Azure infrastructure required to run Cycas')
    print('  and configures it by writing the connection strings to your .env file.')
    print()

    saved = load_state()
    if saved:
        run_mode = saved.get('run_mode', 'azurefunction')
    else:
        run_mode = 'azurefunction'

    total = '8' if run_mode == 'azurefunction' else '5'

    print()
    print('  What will be created:')
    print('    - Azure Data Explorer cluster + database')
    print('    - Storage account (blob container, queue, table)')
    print('    - Input source configuration (Blob, SAS, and/or SFTP)')
    if run_mode == 'azurefunction':
        print('    - Two Azure Function Apps (watcher + processor)')
        print('    - (Optional) Key Vault for ZIP password storage')
    print('    - Offline Velociraptor collector (Windows.Triage.Targets -> input container)')
    print()
    print('  Progress is saved automatically. If the script is interrupted,')
    print('  re-run it and choose to resume the saved session.')
    print()

    defaults = read_env_defaults(ENV_EXAMPLE)

    section(f'Step 1/{total} — Authentication')
    azure      = AzureManager()
    credential = azure.authenticate()

    if saved:
        completed = saved.get('completed', False)
        section('Previous installation found' if completed else 'Saved session found')
        mode_label = 'Azure Functions' if run_mode == 'azurefunction' else 'Local'
        info(f'Deployment mode: {mode_label}')
        info(f'Subscription : {saved["subscription_id"]}')
        info(f'Resource group: {saved["resource_group"]} ({saved["location"]})')
        info(f'ADX cluster  : {saved["cluster_name"]} / {saved["database_name"]}')
        info(f'Storage      : {saved["account_name"]}')
        if run_mode == 'azurefunction':
            info(f'Functions    : {saved["watcher_app"]}, {saved["processor_app"]}')
        print()
        prompt_text = '  Re-run with this configuration? [Y/n]: ' if completed else '  Resume from saved session? [Y/n]: '
        resume = input(prompt_text).strip().lower()
        if resume not in ('n', 'no'):
            subscription_id = saved['subscription_id']
            resource_group  = saved['resource_group']
            location        = saved['location']
            new_rg          = saved['new_rg']
            cluster_name    = saved['cluster_name']
            database_name   = saved['database_name']
            sku_name        = saved['sku_name']
            sku_tier        = saved['sku_tier']
            admin_users     = saved['admin_users']
            account_name     = saved['account_name']
            table_name       = saved['table_name']
            queue_name       = saved['queue_name']
            container        = saved['container']
            status_container = saved['status_container']
            config_container = saved.get('config_container', 'config')
            input_sources    = saved['input_sources']
            if run_mode == 'azurefunction':
                watcher_app                = saved['watcher_app']
                watcher_sa                 = saved['watcher_sa']
                processor_app              = saved['processor_app']
                processor_sa               = saved['processor_sa']
                keyvault_name              = saved['keyvault_name']
                keyvault_password_location = saved['keyvault_password_location']
                insights_name              = saved['insights_name']
                webapp_mode                = saved.get('webapp_mode')
                webapp_app                 = saved['webapp_app']
                webapp_allowed_ips         = saved['webapp_allowed_ips']
                zip_password               = None  # not stored in state; provision_collector reads it from Key Vault
            else:
                watcher_app = watcher_sa = processor_app = processor_sa = insights_name = None
                keyvault_name = keyvault_password_location = zip_password = None
                webapp_mode = webapp_app = webapp_allowed_ips = None
        else:
            clear_state()
            saved = None
            run_mode = 'azurefunction'

    if not saved:
        subscription_id = azure.select_subscription()

        setup_mode = collect_setup_mode()

        if setup_mode == 'advanced':
            section('Deployment Mode')
            run_mode = collect_run_mode()
            total = '8' if run_mode == 'azurefunction' else '5'

        resource_group, location, new_rg = collect_resource_group(azure, subscription_id, f'2/{total}')

        adx = AdxManager(credential, adx_cluster_uri='', adx_cluster_ingestion_uri='', adx_database_name='')

        if setup_mode == 'easy':
            (account_name, table_name, queue_name, container, status_container,
             cluster_name, database_name,
             watcher_app, watcher_sa, processor_app, processor_sa, insights_name,
             keyvault_name) = auto_generate_names(resource_group, defaults)
            config_container = defaults.get('blob_container_config', 'config')
            sku_name, sku_tier, _ = SKUS['1']
            admin_users = []
            blob_uri = f'https://{account_name}.blob.core.windows.net'
            input_sources = {
                'blob': {
                    'BLOB_STORAGEACCOUNT_ENABLED': 'true',
                    'BLOB_STORAGEACCOUNT_URI':     blob_uri,
                    'BLOB_CONTAINER_INPUT':        container,
                }
            }
        else:
            cluster_name, database_name, sku_name, sku_tier = collect_adx_config(defaults, resource_group, f'3/{total}')
            adx.adx_database_name = database_name
            admin_users = collect_admin_users(adx)
            account_name, table_name, queue_name, container, status_container, config_container = collect_storage_config(defaults, resource_group, f'4/{total}')
            input_sources = collect_input_sources(defaults, account_name, container, f'5/{total}')
            if run_mode == 'azurefunction':
                watcher_app, watcher_sa, processor_app, processor_sa, insights_name = collect_functions_config(resource_group, f'6/{total}')
            else:
                watcher_app = watcher_sa = processor_app = processor_sa = insights_name = None

        if run_mode == 'azurefunction':
            if setup_mode == 'easy':
                keyvault_password_location = KEYVAULT_PASSWORD_LOCATION
                zip_password = None
                webapp_mode, webapp_app, webapp_allowed_ips = 'local', None, None
                info('Web app configured for local use (optional - run with gunicorn when needed).')
            else:
                keyvault_name, keyvault_password_location, zip_password = collect_keyvault_config(resource_group, f'7/{total}')
                webapp_mode, webapp_app, webapp_allowed_ips = collect_webapp_config(resource_group, f'8/{total}')
        else:
            keyvault_name = keyvault_password_location = zip_password = None
            webapp_mode = webapp_app = webapp_allowed_ips = None

        state = {
            'run_mode':                  run_mode,
            'subscription_id':           subscription_id,
            'resource_group':            resource_group,
            'location':                  location,
            'new_rg':                    new_rg,
            'cluster_name':              cluster_name,
            'database_name':             database_name,
            'sku_name':                  sku_name,
            'sku_tier':                  sku_tier,
            'admin_users':               admin_users,
            'account_name':              account_name,
            'table_name':                table_name,
            'queue_name':                queue_name,
            'container':                 container,
            'status_container':          status_container,
            'config_container':          config_container,
            'input_sources':             input_sources,
        }
        if run_mode == 'azurefunction':
            state.update({
                'watcher_app':               watcher_app,
                'watcher_sa':                watcher_sa,
                'processor_app':             processor_app,
                'processor_sa':              processor_sa,
                'keyvault_name':             keyvault_name,
                'keyvault_password_location': keyvault_password_location,
                'insights_name':             insights_name,
                'webapp_mode':               webapp_mode,
                'webapp_app':                webapp_app,
                'webapp_allowed_ips':        webapp_allowed_ips,
            })
        save_state(state)

    if not confirm_plan(run_mode,
                        resource_group, location, new_rg,
                        cluster_name, database_name, sku_name,
                        admin_users,
                        account_name, table_name, queue_name, container, status_container, config_container,
                        input_sources,
                        watcher_app, watcher_sa, processor_app, processor_sa,
                        keyvault_name, keyvault_password_location,
                        webapp_mode, webapp_app, webapp_allowed_ips):
        print('Aborted.')
        sys.exit(0)

    provision_storage(azure, credential, subscription_id,
                      resource_group, location, new_rg,
                      account_name, table_name, queue_name, container, status_container,
                      config_container,
                      defaults)

    step('Writing input source settings to .env...')
    for source_settings in input_sources.values():
        write_env_values(source_settings)
    success('Input source settings written.')

    if run_mode == 'azurefunction' and keyvault_name:
        adx_tmp = AdxManager(credential, '', '', '')
        principal_id, _ = adx_tmp.get_current_user_id()
        provision_keyvault(credential, subscription_id, resource_group, location,
                           keyvault_name, keyvault_password_location, zip_password,
                           principal_id)

    provision_collector(credential, subscription_id, resource_group,
                        account_name, container,
                        keyvault_name, keyvault_password_location, zip_password,
                        read_env_defaults(ENV_FILE))

    provision_adx(credential, subscription_id,
                  resource_group, location,
                  cluster_name, database_name, sku_name, sku_tier,
                  admin_users,
                  defaults)

    if run_mode == 'azurefunction':
        provision_functions(credential, subscription_id,
                            resource_group, location,
                            account_name,
                            cluster_name,
                            watcher_app, watcher_sa,
                            processor_app, processor_sa,
                            keyvault_name, insights_name)

        if webapp_mode == 'local':
            step('Assigning storage roles to current user for local webapp...')
            from core.manager.functions import FunctionsManager
            funcs_tmp = FunctionsManager(credential, subscription_id)
            adx_tmp2  = AdxManager(credential, '', '', '')
            current_user_id, _ = adx_tmp2.get_current_user_id()
            funcs_tmp.assign_data_storage_roles(resource_group, account_name, current_user_id, principal_type='User')
            success('Storage roles assigned to current user.')
        elif webapp_mode == 'azure' and webapp_app:
            provision_webapp_all(credential, subscription_id,
                                 resource_group, location,
                                 account_name, status_container, config_container, webapp_app, webapp_allowed_ips)

    state = load_state()
    if state:
        state['completed'] = True
        save_state(state)

    print()
    if run_mode == 'azurefunction':
        success('Installation complete.')
        print()
        if webapp_mode == 'local':
            info('To run the webapp locally (optional, but recommended):')
            info('  cd <repo root>')
            info('  gunicorn --bind=0.0.0.0:8000 --timeout 600 webapp.app:app')
            wsl_ip = _get_wsl_ip()
            if wsl_ip:
                info(f'  Then open http://{wsl_ip}:8000 in your browser (WSL IP).')
            else:
                info('  Then open http://localhost:8000 in your browser.')
            print()
    else:
        success('Installation complete.')
        print()
        info('Run `zip2adx.py` to launch the script locally.')
        print()
    
    info('Place triage ZIPs in any of the configured input sources to start processing:')
    if 'blob' in input_sources:
        info(f'  Blob  — {account_name} / {container}')
    if 'sas' in input_sources:
        info(f'  SAS   — container configured via SAS URL')
    if 'sftp' in input_sources:
        sftp = input_sources['sftp']
        info(f'  SFTP  — {sftp["SFTP_USERNAME"]}@{sftp["SFTP_URL"]}:{sftp["SFTP_PORT"]}')
    if webapp_app:
        print()
        info(f'Web application:')
        info(f'  URL     — https://{webapp_app}.azurewebsites.net')
        info(f'  Access  — restricted to: {", ".join(webapp_allowed_ips)}')

    collector = next((ROOT / 'build' / 'collector' / 'datastore').glob('Collector_*'), None)
    if collector:
        print()
        info(f'Offline collector — {collector}')
    info('')


if __name__ == '__main__':
    main()
