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

import logging
import re
import sys
from pathlib import Path

from core.manager.azure import AzureManager
from core.manager.adx import AdxManager
from core.manager.blob import BlobManager
from core.manager.table import TablestorageManager
from core.manager.queue import QueueManager

logging.basicConfig(level=logging.WARNING, format='%(message)s')

ROOT        = Path(__file__).parent
ENV_FILE    = ROOT / '.env'
ENV_EXAMPLE = ROOT / '.env_example'

SKUS = {
    '1': ('Dev(No SLA)_Standard_E2a_v4', 'Basic',    'development / testing (cheapest). Estimated cost ~$2.40/day '),
    '2': ('Standard_D11_v2',             'Standard', 'production. Estimated cost ~$5.50/day'),
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
        print(f'  Enter a number, M for more, or press Enter for default.')


def update_env_section(pattern, block):
    if not ENV_FILE.exists():
        ENV_FILE.write_text(ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else '')
        info('Created .env from .env_example' if ENV_EXAMPLE.exists() else 'Created empty .env')

    content = ENV_FILE.read_text()
    if re.search(pattern, content):
        content = re.sub(pattern, block, content)
    else:
        content = block + '\n' + content
    ENV_FILE.write_text(content)


# ---------------------------------------------------------------------------
# Resource group selection
# ---------------------------------------------------------------------------

def select_resource_group(azure, subscription_id):
    section('Step 2/4 — Resource Group')

    step('Fetching resource groups...')
    rgs = azure.list_resource_groups(subscription_id)

    print('Available resource groups:')
    for i, rg in enumerate(rgs, 1):
        print(f'  {i}) {rg["name"]} ({rg["location"]})')
    print(f'  N) Create a new resource group')
    print()

    while True:
        choice = input('Select resource group: ').strip().lower()
        if choice == 'n':
            name     = prompt('New resource group name')
            location = select_location(azure, subscription_id)
            azure.create_resource_group(subscription_id, name, location)
            success(f"Resource group '{name}' created.")
            return name, location
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(rgs):
                rg = rgs[idx]
                return rg['name'], rg['location']
        except ValueError:
            pass
        print(f'  Enter a number between 1 and {len(rgs)}, or N to create a new one.')


# ---------------------------------------------------------------------------
# ADX cluster admin selection
# ---------------------------------------------------------------------------

def select_cluster_admins(adx, resource_group, cluster_name):
    print()
    add_more = input('  Add additional cluster admins (AllDatabasesAdmin)? [y/N]: ').strip().lower()
    if add_more not in ('y', 'yes'):
        return

    assigned = []

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

        choice = input('  Select user to add [number, or Enter to search again]: ').strip()
        if not choice:
            continue

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(users):
                user = users[idx]
                assignment_name = f'cycas-admin-{user["id"][:8]}'
                step(f"Assigning AllDatabasesAdmin to '{user['displayName']}'...")
                adx.assign_cluster_admin(resource_group, cluster_name, user['id'], 'User', assignment_name)
                success(f"'{user['displayName']}' added as cluster admin.")
                assigned.append(user['displayName'])
        except (ValueError, IndexError):
            info('Invalid selection.')

    if assigned:
        info(f"Cluster admins added: {', '.join(assigned)}")


# ---------------------------------------------------------------------------
# ADX setup
# ---------------------------------------------------------------------------

def setup_adx(azure, credential, subscription_id, resource_group, location, defaults):
    section('Step 3/4 — Azure Data Explorer')

    cluster_name  = prompt('Cluster name (lowercase letters and numbers)', default='cycasadx')
    database_name = prompt('Database name', default=defaults.adx_database_name)

    print('\nSKU:')
    for key, (sku, _, desc) in SKUS.items():
        print(f'  {key}) {sku:<45} {desc}')
    sku_choice = prompt('Select SKU', default='1')
    sku_name, sku_tier, _ = SKUS.get(sku_choice, SKUS['1'])

    print()
    info(f'Resource group : {resource_group}')
    info(f'Location       : {location}')
    info(f'Cluster        : {cluster_name}')
    info(f'Database       : {database_name}')
    info(f'SKU            : {sku_name}')
    print()

    confirm = input('Proceed? [Y/n]: ').strip().lower()
    if confirm not in ('', 'y', 'yes'):
        print('Skipped ADX setup.')
        return

    adx = AdxManager(credential, adx_cluster_uri='', adx_cluster_ingestion_uri='', adx_database_name=database_name)

    step('Creating ADX cluster (this may take more than 10 minutes)...')
    cluster = adx.provision_cluster(subscription_id, resource_group, location, cluster_name, sku_name, sku_tier)
    success(f'Cluster ready: {cluster.uri}')

    step(f"Creating database '{database_name}'...")
    adx.provision_database(resource_group, cluster_name, database_name, location)
    success(f"Database '{database_name}' created.")

    step('Assigning database admin permissions to current user...')
    principal_id, principal_type = adx.get_current_user_id()
    adx.assign_database_admin(resource_group, cluster_name, database_name, principal_id, principal_type)

    select_cluster_admins(adx, resource_group, cluster_name)

    step('Updating .env...')
    adx_block = (
        '# ADX variables\n'
        'ADX_CLUSTER_ENABLED=true\n'
        f'ADX_CLUSTER_URI={cluster.uri}\n'
        f'ADX_CLUSTER_INGESTION_URI={cluster.data_ingestion_uri}\n'
        f'ADX_DATABASE_NAME={database_name}\n'
    )
    update_env_section(r'# ADX variables\n(?:.*\n)*?ADX_DATABASE_NAME=.*\n?', adx_block)
    success('.env updated with ADX configuration.')


# ---------------------------------------------------------------------------
# Storage setup
# ---------------------------------------------------------------------------

def setup_storage(credential, subscription_id, resource_group, location, defaults):
    section('Step 4/4 — Storage Account')

    account_name = prompt('Storage account name (3-24 lowercase alphanumeric)', default='cycasstorage')
    table_name   = prompt('Status table name', default=defaults.blob_logtable_name)
    queue_name   = prompt('Queue name',        default=defaults.blob_queue_name)
    container    = prompt('Blob container for uploads', default=defaults.blob_container_input)

    table_endpoint = f'https://{account_name}.table.core.windows.net'
    queue_url      = f'https://{account_name}.queue.core.windows.net'
    blob_uri       = f'https://{account_name}.blob.core.windows.net'

    print()
    info(f'Storage account : {account_name}')
    info(f'Table           : {table_name}  ({table_endpoint})')
    info(f'Queue           : {queue_name}  ({queue_url})')
    info(f'Blob container  : {container}')
    print()

    confirm = input('Proceed? [Y/n]: ').strip().lower()
    if confirm not in ('', 'y', 'yes'):
        print('Skipped storage setup.')
        return

    step('Creating storage account...')
    blob_mgr = BlobManager(credential, blob_uri)
    blob_mgr.provision_storage_account(subscription_id, resource_group, location, account_name)
    success(f"Storage account '{account_name}' ready.")

    step(f"Creating status table '{table_name}'...")
    table_mgr = TablestorageManager(credential, table_endpoint, table_name)
    table_mgr.authenticate(verify_enabled=False)
    success(f"Table '{table_name}' ready.")

    step(f"Creating queue '{queue_name}'...")
    queue_mgr = QueueManager(credential, queue_url, queue_name)
    queue_mgr.authenticate(verify_enabled=False)
    success(f"Queue '{queue_name}' ready.")

    step(f"Creating blob container '{container}'...")
    blob_mgr.authenticate()
    blob_mgr.create_container(container)
    success(f"Container '{container}' ready.")

    step('Updating .env...')
    storage_block = (
        '# Storage account variables used for updating the status\n'
        'BLOB_LOGTABLE_ENABLED=true\n'
        f'BLOB_LOGTABLE_URI={table_endpoint}\n'
        f'BLOB_LOGTABLE_NAME={table_name}\n'
        '\n'
        '# Blob queue variables used by Azure Functions\n'
        'BLOB_QUEUE_ENABLED=true\n'
        f'BLOB_QUEUE_URL={queue_url}\n'
        f'BLOB_QUEUE_NAME={queue_name}\n'
        '\n'
        '# Blob - Azure Blob Storage access via storage account credentials or managed identity.\n'
        'BLOB_STORAGEACCOUNT_ENABLED=true\n'
        f'BLOB_STORAGEACCOUNT_URI={blob_uri}\n'
        f'BLOB_CONTAINER_INPUT={container}\n'
    )
    update_env_section(
        r'# Storage account variables.*\n(?:.*\n)*?BLOB_LOGTABLE_NAME=.*\n?',
        storage_block
    )
    success('.env updated with storage configuration.')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    section('Cycas Install')

    defaults = load_config(str(ENV_EXAMPLE))

    section('Step 1/4 — Authentication')
    azure           = AzureManager()
    credential      = azure.authenticate()
    subscription_id = azure.select_subscription()

    resource_group, location = select_resource_group(azure, subscription_id)

    setup_adx(azure, credential, subscription_id, resource_group, location, defaults)
    setup_storage(credential, subscription_id, resource_group, location, defaults)

    # Future sections:
    # setup_azure_functions(credential, subscription_id, resource_group, location)

    print()
    success('Installation complete. Review your .env file before running Cycas.')


if __name__ == '__main__':
    main()
