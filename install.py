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

import json
import logging
import os
import re
import sys
import random
import string
from pathlib import Path

from core.manager.azure import AzureManager
from core.manager.adx import AdxManager
from core.manager.blob import BlobManager
from core.manager.table import TablestorageManager
from core.manager.queue import QueueManager
from core.manager.functions import FunctionsManager, build_function_zip

logging.basicConfig(level=logging.WARNING, format='%(message)s')

ROOT        = Path(__file__).parent
ENV_FILE    = ROOT / '.env'
ENV_EXAMPLE = ROOT / '.env_example'
STATE_FILE  = ROOT / '.install_state.json'

SKUS = {
    '1': ('Dev(No SLA)_Standard_E2a_v4', 'Basic',    'development / testing ~$2.40/day'),
    '2': ('Standard_D11_v2',             'Standard', 'production ~$5.50/day per node'),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section(title): print(f'\n{"="*60}\n  {title}\n{"="*60}\n')
def step(msg):      print(f'  >> {msg}')
def info(msg):      print(f'     {msg}')
def success(msg):   print(f'  [OK] {msg}')
def error(msg):     print(f'  [ERROR] {msg}', file=sys.stderr); sys.exit(1)


def prompt(label, default=None):
    suffix = f' [{default}]' if default is not None else ''
    while True:
        value = input(f'{label}{suffix}: ').strip()
        if value:
            return value
        if default is not None:
            return default
        print('  This field is required.')


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
        required = {
            'subscription_id', 'resource_group', 'location', 'new_rg',
            'cluster_name', 'database_name', 'sku_name', 'sku_tier',
            'admin_users', 'account_name', 'table_name', 'queue_name',
            'container', 'watcher_app', 'watcher_sa', 'processor_app', 'processor_sa',
            'keyvault_name',
        }
        return data if required.issubset(data) else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def clear_state():
    STATE_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phase 1: Collect all inputs
# ---------------------------------------------------------------------------

def collect_resource_group(azure, subscription_id):
    section('Step 2/5 — Resource Group')

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


def collect_adx_config(defaults, resource_group):
    section('Step 3/5 — Azure Data Explorer')

    rg_slug = re.sub(r'[^a-z0-9]', '', resource_group.lower())
    cluster_name  = prompt('Cluster name (lowercase letters and numbers)', default=f'{rg_slug}-adx01')
    database_name = prompt('Database name', default=defaults.get('adx_database_name', 'dfir'))

    print('\nSKU:')
    for key, (sku, _, desc) in SKUS.items():
        print(f'  {key}) {sku:<45} {desc}')
    sku_choice = prompt('Select SKU', default='1')
    sku_name, sku_tier, _ = SKUS.get(sku_choice, SKUS['1'])

    return cluster_name, database_name, sku_name, sku_tier


def collect_admin_users(adx):
    admins = []

    print()
    add_admins = input('  Add cluster admins (AllDatabasesAdmin)? [Y/n]: ').strip().lower()
    if add_admins in ('y', 'yes'):
        return admins

    while True:
        print()
        query = input('  Search user by name or email (or press Enter to finish): ').strip()
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


def collect_functions_config(resource_group):
    section('Step 5/5 — Azure Functions')

    info('Each function app gets its own dedicated storage account (Azure best practice).')
    print()

    watcher_app   = prompt('Watcher function app name',   default=f'{resource_group}-watcher-af')
    watcher_clean = re.sub(r'[^a-z0-9]', '', watcher_app.lower())
    watcher_sa    = prompt('Watcher storage account name (3-24 alphanumeric)',
                           default=watcher_clean[:18] + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))

    processor_app = prompt('Processor function app name', default=f'{resource_group}-processor-af')
    processor_clean = re.sub(r'[^a-z0-9]', '', processor_app.lower())
    processor_sa  = prompt('Processor storage account name (3-24 alphanumeric)',
                           default=processor_clean[:18] + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))

    print()
    keyvault_name = input('Optional: Key Vault name for storing password of zip files with triagedata (press Enter to skip): ').strip()

    return watcher_app, watcher_sa, processor_app, processor_sa, keyvault_name


def collect_storage_config(defaults, resource_group):
    section('Step 4/5 — Storage Account')

    rg_slug = re.sub(r'[^a-z0-9]', '', resource_group.lower())
    account_name = prompt('Storage account name (3-24 lowercase alphanumeric)', default=(rg_slug + 'zip')[:18] + ''.join(random.choices(string.ascii_lowercase + string.digits, k=6)))
    table_name   = prompt('Status table name',       default=defaults.get('blob_logtable_name', 'statusupdate'))
    queue_name   = prompt('Queue name',              default=defaults.get('blob_queue_name', 'triagepackages'))
    container    = prompt('Blob container for uploads', default=defaults.get('blob_container_input', 'uploads'))

    return account_name, table_name, queue_name, container


# ---------------------------------------------------------------------------
# Phase 2: Confirm
# ---------------------------------------------------------------------------

def confirm_plan(resource_group, location, new_rg,
                 cluster_name, database_name, sku_name,
                 admin_users,
                 account_name, table_name, queue_name, container,
                 watcher_app, watcher_sa, processor_app, processor_sa,
                 keyvault_name):

    section('Summary — Review before provisioning')

    print('  Resource group')
    info(f'Name     : {resource_group}  {"(will be created)" if new_rg else "(existing)"}')
    info(f'Location : {location}')

    print('\n  Azure Data Explorer')
    info(f'Cluster  : {cluster_name}')
    info(f'Database : {database_name}')
    info(f'SKU      : {sku_name}')
    if admin_users:
        info(f'Admins   : {", ".join(u["displayName"] for u in admin_users)}')

    print('\n  Storage Account (data)')
    info(f'Account  : {account_name}')
    info(f'Table    : {table_name}')
    info(f'Queue    : {queue_name}')
    info(f'Container: {container}')

    print('\n  Azure Functions')
    info(f'Watcher  : {watcher_app}  (storage: {watcher_sa})')
    info(f'Processor: {processor_app}  (storage: {processor_sa})')
    if keyvault_name:
        info(f'Key Vault: {keyvault_name}')

    print()
    confirm = input('Proceed with provisioning? [Y/n]: ').strip().lower()
    return confirm in ('', 'y', 'yes')


# ---------------------------------------------------------------------------
# Phase 3: Provision
# ---------------------------------------------------------------------------

def provision_all(azure, credential, subscription_id,
                  resource_group, location, new_rg,
                  cluster_name, database_name, sku_name, sku_tier,
                  admin_users,
                  account_name, table_name, queue_name, container):

    section('Provisioning')

    if new_rg:
        step(f"Creating resource group '{resource_group}'...")
        azure.create_resource_group(subscription_id, resource_group, location)
        success(f"Resource group '{resource_group}' created.")

    adx = AdxManager(credential, adx_cluster_uri='', adx_cluster_ingestion_uri='', adx_database_name=database_name)

    step('Creating ADX cluster (this may take more than 10 minutes)...')
    cluster = adx.provision_cluster(subscription_id, resource_group, location, cluster_name, sku_name, sku_tier)
    success(f'Cluster ready: {cluster.uri}')

    step(f"Creating database '{database_name}'...")
    adx.provision_database(resource_group, cluster_name, database_name, location)
    success(f"Database '{database_name}' created.")

    step('Assigning AllDatabasesAdmin to current user...')
    principal_id, principal_type = adx.get_current_user_id()
    adx.AllDatabasesAdmin(resource_group, cluster_name, principal_id, principal_type)
    success('Current user assigned as cluster AllDatabasesAdmin.')

    for user in admin_users:
        step(f"Assigning AllDatabasesAdmin to '{user['displayName']}'...")
        assignment_name = f'cycas-admin-{user["id"][:8]}'
        adx.assign_cluster_admin(resource_group, cluster_name, user['id'], 'User', assignment_name)
        success(f"'{user['displayName']}' assigned as cluster admin.")

    blob_uri       = f'https://{account_name}.blob.core.windows.net'
    table_endpoint = f'https://{account_name}.table.core.windows.net'
    queue_url      = f'https://{account_name}.queue.core.windows.net'

    step(f"Creating storage account '{account_name}'...")
    blob_mgr = BlobManager(credential, blob_uri)
    blob_mgr.provision_storage_account(subscription_id, resource_group, location, account_name)
    success(f"Storage account '{account_name}' ready.")

    step(f"Creating table '{table_name}'...")
    TablestorageManager(credential, table_endpoint, table_name).authenticate(verify_enabled=False)
    success(f"Table '{table_name}' ready.")

    step(f"Creating queue '{queue_name}'...")
    QueueManager(credential, queue_url, queue_name).authenticate(verify_enabled=False)
    success(f"Queue '{queue_name}' ready.")

    step(f"Creating blob container '{container}'...")
    blob_mgr.authenticate()
    blob_mgr.create_container(container)
    success(f"Container '{container}' ready.")

    step('Writing .env...')

    write_env_values({
        'ADX_CLUSTER_ENABLED':       'true',
        'ADX_CLUSTER_URI':           cluster.uri,
        'ADX_CLUSTER_INGESTION_URI': cluster.data_ingestion_uri,
        'ADX_DATABASE_NAME':         database_name,
    })

    write_env_values({
        'BLOB_LOGTABLE_ENABLED':      'true',
        'BLOB_LOGTABLE_URI':          table_endpoint,
        'BLOB_LOGTABLE_NAME':         table_name,
        'BLOB_QUEUE_ENABLED':         'true',
        'BLOB_QUEUE_URL':             queue_url,
        'BLOB_QUEUE_NAME':            queue_name,
        'BLOB_STORAGEACCOUNT_ENABLED': 'true',
        'BLOB_STORAGEACCOUNT_URI':    blob_uri,
        'BLOB_CONTAINER_INPUT':       container,
    })

    success('.env updated.')


# ---------------------------------------------------------------------------
# Phase 4: Azure Functions
# ---------------------------------------------------------------------------

def provision_functions(credential, subscription_id,
                        resource_group, location,
                        data_account,
                        watcher_app, watcher_sa,
                        processor_app, processor_sa,
                        keyvault_name):

    section('Provisioning — Azure Functions')

    funcs = FunctionsManager(credential, subscription_id)

    # Dedicated storage accounts (one per function app — Azure best practice)
    step(f"Creating watcher storage account '{watcher_sa}'...")
    BlobManager(credential, f'https://{watcher_sa}.blob.core.windows.net').provision_storage_account(
        subscription_id, resource_group, location, watcher_sa
    )
    success(f"'{watcher_sa}' ready.")

    step(f"Creating processor storage account '{processor_sa}'...")
    BlobManager(credential, f'https://{processor_sa}.blob.core.windows.net').provision_storage_account(
        subscription_id, resource_group, location, processor_sa
    )
    success(f"'{processor_sa}' ready.")

    watcher_conn   = funcs.get_storage_connection_string(resource_group, watcher_sa)
    processor_conn = funcs.get_storage_connection_string(resource_group, processor_sa)

    # Deployment containers (Flex Consumption runs directly from a blob container)
    step("Creating deployment containers...")
    funcs.create_deployment_container(watcher_conn)
    funcs.create_deployment_container(processor_conn)
    success("Deployment containers ready.")

    watcher_deploy_url   = f'https://{watcher_sa}.blob.core.windows.net/deployments'
    processor_deploy_url = f'https://{processor_sa}.blob.core.windows.net/deployments'

    # Flex Consumption plans are 1-to-1 with function apps — one plan each
    watcher_plan_name   = f'{watcher_app}-plan'
    processor_plan_name = f'{processor_app}-plan'

    step(f"Creating App Service Plan '{watcher_plan_name}'...")
    watcher_plan = funcs.provision_app_service_plan(resource_group, watcher_plan_name, location)
    success(f"Plan '{watcher_plan_name}' ready.")

    step(f"Creating App Service Plan '{processor_plan_name}'...")
    processor_plan = funcs.provision_app_service_plan(resource_group, processor_plan_name, location)
    success(f"Plan '{processor_plan_name}' ready.")

    # Function apps (watcher: 512 MB — light work; processor: 2048 MB — heavy work)
    step(f"Creating watcher function app '{watcher_app}' (512 MB)...")
    watcher_result = funcs.provision_function_app(
        resource_group, watcher_app, location, watcher_plan['id'],
        instance_memory_mb=512,
        deployment_container_url=watcher_deploy_url,
        conn_str=watcher_conn,
    )
    success(f"'{watcher_app}' created.")

    step(f"Creating processor function app '{processor_app}' (2048 MB)...")
    processor_result = funcs.provision_function_app(
        resource_group, processor_app, location, processor_plan['id'],
        instance_memory_mb=2048,
        deployment_container_url=processor_deploy_url,
        conn_str=processor_conn,
    )
    success(f"'{processor_app}' created.")

    watcher_principal   = watcher_result.get('identity', {}).get('principalId')
    processor_principal = processor_result.get('identity', {}).get('principalId')

    # RBAC: grant both apps access to the data storage account
    step("Assigning data storage roles to watcher managed identity...")
    funcs.assign_data_storage_roles(resource_group, data_account, watcher_principal)
    success('Watcher storage roles assigned.')

    step("Assigning data storage roles to processor managed identity...")
    funcs.assign_data_storage_roles(resource_group, data_account, processor_principal)
    success('Processor storage roles assigned.')

    if keyvault_name:
        step(f"Assigning Key Vault Secrets User to watcher on '{keyvault_name}'...")
        funcs.assign_keyvault_roles(resource_group, keyvault_name, watcher_principal)
        success('Watcher Key Vault role assigned.')

        step(f"Assigning Key Vault Secrets User to processor on '{keyvault_name}'...")
        funcs.assign_keyvault_roles(resource_group, keyvault_name, processor_principal)
        success('Processor Key Vault role assigned.')

    # App settings: read the final .env and convert to Azure app settings
    env_raw      = read_env_defaults(ENV_FILE)
    env_settings = {k.upper(): v for k, v in env_raw.items() if v}

    queue_service_uri = f'https://{data_account}.queue.core.windows.net'

    watcher_settings = {
        'AzureWebJobsStorage':  watcher_conn,
        'CYCAS_DEPLOY_STORAGE': watcher_conn,
        **env_settings,
    }
    # Processor also needs the managed-identity connection for its queue trigger
    processor_settings = {
        'AzureWebJobsStorage':                processor_conn,
        'CYCAS_DEPLOY_STORAGE':               processor_conn,
        'CYCAS_DATASTORAGE__queueServiceUri': queue_service_uri,
        'CYCAS_DATASTORAGE__credential':      'managedidentity',
        **env_settings,
    }

    step(f"Applying app settings to '{watcher_app}'...")
    funcs.set_app_settings(resource_group, watcher_app, watcher_settings)
    success('Watcher settings applied.')

    step(f"Applying app settings to '{processor_app}'...")
    funcs.set_app_settings(resource_group, processor_app, processor_settings)
    success('Processor settings applied.')

    # Deploy code (ZIP includes function files + core/ package)
    root     = Path(__file__).parent
    core_dir = root / 'core'

    step('Building watcher deployment ZIP...')
    watcher_zip = build_function_zip(root / 'azurefunctions' / 'watcher', core_dir)
    step(f"Deploying watcher to '{watcher_app}'...")
    funcs.deploy_zip(resource_group, watcher_app, watcher_zip, watcher_conn, 512)
    os.unlink(watcher_zip)
    success('Watcher deployed.')

    step('Building processor deployment ZIP...')
    processor_zip = build_function_zip(root / 'azurefunctions' / 'processor', core_dir)
    step(f"Deploying processor to '{processor_app}'...")
    funcs.deploy_zip(resource_group, processor_app, processor_zip, processor_conn, 2048)
    os.unlink(processor_zip)
    success('Processor deployed.')

    info('')
    info('Note: RBAC role propagation can take a few minutes. If the processor')
    info('queue trigger fails on first run, wait 2-3 minutes and try again.')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    section('Cycas Install')

    section('Step 1/5 — Authentication')
    azure      = AzureManager()
    credential = azure.authenticate()

    saved = load_state()
    if saved:
        section('Saved session found')
        info(f'Subscription : {saved["subscription_id"]}')
        info(f'Resource group: {saved["resource_group"]} ({saved["location"]})')
        info(f'ADX cluster  : {saved["cluster_name"]} / {saved["database_name"]}')
        info(f'Storage      : {saved["account_name"]}')
        info(f'Functions    : {saved["watcher_app"]}, {saved["processor_app"]}')
        print()
        resume = input('  Resume from saved session? [Y/n]: ').strip().lower()
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
            account_name    = saved['account_name']
            table_name      = saved['table_name']
            queue_name      = saved['queue_name']
            container       = saved['container']
            watcher_app     = saved['watcher_app']
            watcher_sa      = saved['watcher_sa']
            processor_app   = saved['processor_app']
            processor_sa    = saved['processor_sa']
            keyvault_name   = saved['keyvault_name']
        else:
            clear_state()
            saved = None

    if not saved:
        defaults = read_env_defaults(ENV_EXAMPLE)

        subscription_id = azure.select_subscription()

        resource_group, location, new_rg = collect_resource_group(azure, subscription_id)

        cluster_name, database_name, sku_name, sku_tier = collect_adx_config(defaults, resource_group)

        adx = AdxManager(credential, adx_cluster_uri='', adx_cluster_ingestion_uri='', adx_database_name=database_name)
        admin_users = collect_admin_users(adx)

        account_name, table_name, queue_name, container = collect_storage_config(defaults, resource_group)

        watcher_app, watcher_sa, processor_app, processor_sa, keyvault_name = collect_functions_config(resource_group)

        save_state({
            'subscription_id': subscription_id,
            'resource_group':  resource_group,
            'location':        location,
            'new_rg':          new_rg,
            'cluster_name':    cluster_name,
            'database_name':   database_name,
            'sku_name':        sku_name,
            'sku_tier':        sku_tier,
            'admin_users':     admin_users,
            'account_name':    account_name,
            'table_name':      table_name,
            'queue_name':      queue_name,
            'container':       container,
            'watcher_app':     watcher_app,
            'watcher_sa':      watcher_sa,
            'processor_app':   processor_app,
            'processor_sa':    processor_sa,
            'keyvault_name':   keyvault_name,
        })

    if not confirm_plan(resource_group, location, new_rg,
                        cluster_name, database_name, sku_name,
                        admin_users,
                        account_name, table_name, queue_name, container,
                        watcher_app, watcher_sa, processor_app, processor_sa,
                        keyvault_name):
        print('Aborted.')
        sys.exit(0)

    provision_all(azure, credential, subscription_id,
                  resource_group, location, new_rg,
                  cluster_name, database_name, sku_name, sku_tier,
                  admin_users,
                  account_name, table_name, queue_name, container)

    provision_functions(credential, subscription_id,
                        resource_group, location,
                        account_name,
                        watcher_app, watcher_sa,
                        processor_app, processor_sa,
                        keyvault_name)

    clear_state()
    print()
    success('Installation complete. Review your .env before running Cycas.')


if __name__ == '__main__':
    main()
