'''
Module for provisioning and deploying Azure Function Apps.

Provides FunctionsManager for creating Flex Consumption App Service Plans,
Function Apps, assigning RBAC roles to managed identities, configuring app
settings, and deploying code via the Azure Functions Core Tools CLI.
'''

import os
import shutil
import subprocess
import tempfile
import time
import uuid
import requests
import logging as log
from pathlib import Path

from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.authorization.models import RoleAssignmentCreateParameters
from azure.mgmt.storage import StorageManagementClient

log = log.getLogger(__name__)

_ARM_API = '2024-04-01'

# Built-in RBAC role IDs (constant across all Azure tenants)
ROLE_BLOB_DATA_CONTRIBUTOR   = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
ROLE_QUEUE_DATA_CONTRIBUTOR  = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
ROLE_TABLE_DATA_CONTRIBUTOR  = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
ROLE_KEYVAULT_SECRETS_USER   = '4633458b-17de-408a-b874-0445c86b69e6'

_STAGE_IGNORE = shutil.ignore_patterns('.venv', '__pycache__', '.git', '.vscode', 'test')


class FunctionsManager:
    def __init__(self, credential, subscription_id):
        self.credential = credential
        self.subscription_id = subscription_id
        self._auth    = AuthorizationManagementClient(credential, subscription_id)
        self._storage = StorageManagementClient(credential, subscription_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _token(self):
        return self.credential.get_token('https://management.azure.com/.default').token

    def _arm(self, method, path, api_version=None, **kwargs):
        '''
        Make an authenticated ARM REST request and poll async operations
        to completion. For PUT requests, re-fetches the final resource
        after the async operation succeeds so callers always get the
        full resource body (including identity.principalId).
        '''
        url = f'https://management.azure.com{path}?api-version={api_version or _ARM_API}'
        headers = {
            'Authorization': f'Bearer {self._token()}',
            'Content-Type': 'application/json',
        }
        resp = requests.request(method, url, headers=headers, timeout=120, **kwargs)
        if not resp.ok:
            try:
                err = resp.json().get('error', resp.json())
                msg = err.get('message', str(err))
            except Exception:
                msg = resp.text
            raise requests.exceptions.HTTPError(
                f'{resp.status_code} {resp.reason} — {msg}', response=resp
            )

        op_url  = resp.headers.get('Azure-AsyncOperation')
        loc_url = resp.headers.get('Location')

        if resp.status_code == 202:
            poll_url = op_url or loc_url
            if poll_url:
                while True:
                    time.sleep(15)
                    poll = requests.get(
                        poll_url,
                        headers={'Authorization': f'Bearer {self._token()}'},
                        timeout=30,
                    )
                    poll.raise_for_status()
                    data   = poll.json()
                    status = data.get('status', '')
                    if status == 'Succeeded':
                        break
                    if status in ('Failed', 'Canceled'):
                        raise RuntimeError(f"ARM operation {status}: {data.get('error', {})}")

            # Re-fetch the actual resource for PUT operations
            if method.upper() == 'PUT':
                final = requests.get(
                    url,
                    headers={'Authorization': f'Bearer {self._token()}'},
                    timeout=30,
                )
                return final.json() if final.content else {}

        return resp.json() if resp.content else {}

    def _rg_path(self, resource_group):
        return f'/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}'

    def _arm_get(self, path, api_version=None):
        '''GET an ARM resource, returning None if it does not exist (404).'''
        url = f'https://management.azure.com{path}?api-version={api_version or _ARM_API}'
        resp = requests.get(url, headers={'Authorization': f'Bearer {self._token()}'}, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def get_storage_connection_string(self, resource_group, account_name):
        '''Return the primary connection string for a storage account.'''
        keys = self._storage.storage_accounts.list_keys(resource_group, account_name)
        key  = keys.keys[0].value
        return (
            f'DefaultEndpointsProtocol=https;'
            f'AccountName={account_name};'
            f'AccountKey={key};'
            f'EndpointSuffix=core.windows.net'
        )

    def create_deployment_container(self, conn_str, container_name='deployments'):
        '''Create the blob container used for Flex Consumption deployment packages.'''
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient.from_connection_string(conn_str)
        try:
            client.create_container(container_name)
            log.info(f"Container '{container_name}' created.")
        except Exception as e:
            if 'ContainerAlreadyExists' in str(e):
                log.info(f"Container '{container_name}' already exists.")
            else:
                raise

    def provision_app_insights(self, resource_group, name, location):
        '''Create an Application Insights resource and return its connection string.'''
        _AI_API = '2020-02-02'
        path = f'{self._rg_path(resource_group)}/providers/microsoft.insights/components/{name}'
        existing = self._arm_get(path, api_version=_AI_API)
        if existing:
            log.info(f"Application Insights '{name}' already exists, skipping creation.")
            return existing['properties']['ConnectionString']
        result = self._arm(
            'PUT', path, api_version=_AI_API,
            json={
                'location': location,
                'kind': 'web',
                'properties': {'Application_Type': 'web'},
            },
        )
        conn_str = result['properties']['ConnectionString']
        log.info(f"Application Insights '{name}' created.")
        return conn_str

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def provision_app_service_plan(self, resource_group, plan_name, location):
        '''Create a Flex Consumption (FC1) App Service Plan.'''
        path = f'{self._rg_path(resource_group)}/providers/Microsoft.Web/serverfarms/{plan_name}'
        existing = self._arm_get(path)
        if existing:
            log.info(f"App Service Plan '{plan_name}' already exists, skipping creation.")
            return existing
        result = self._arm(
            'PUT',
            f'{self._rg_path(resource_group)}/providers/Microsoft.Web/serverfarms/{plan_name}',
            json={
                'location': location,
                'kind': 'functionapp',
                'sku': {'tier': 'FlexConsumption', 'name': 'FC1'},
                'properties': {'reserved': True},
            },
        )
        log.info(f"Flex Consumption plan '{plan_name}' ready.")
        return result

    def provision_function_app(self, resource_group, app_name, location, plan_id,
                               instance_memory_mb, deployment_container_url):
        '''
        Create a Flex Consumption Python 3.12 Function App with system-assigned
        managed identity.

        plan_id must reference an FC1 (FlexConsumption) App Service Plan —
        serverFarmId is required by the Flex Consumption ARM API.
        The deployment package lives in deployment_container_url (a blob container
        URL). conn_str is the connection string for that storage account, stored
        as the CYCAS_DEPLOY_STORAGE app setting which the Functions runtime uses
        to authenticate to the container.
        '''
        path = f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}'
        existing = self._arm_get(path)
        if existing:
            log.info(f"Function app '{app_name}' already exists, skipping creation.")
            return existing
        result = self._arm(
            'PUT',
            f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}',
            json={
                'location': location,
                'kind': 'functionapp,linux',
                'identity': {'type': 'SystemAssigned'},
                'properties': {
                    'serverFarmId': plan_id,
                    'httpsOnly': True,
                    'autoGeneratedDomainNameLabelScope': 'TenantReuse',
                    'functionAppConfig': {
                        'deployment': {
                            'storage': {
                                'type': 'blobContainer',
                                'value': deployment_container_url,
                                'authentication': {
                                    'type': 'StorageAccountConnectionString',
                                    'storageAccountConnectionStringName': 'CYCAS_DEPLOY_STORAGE',
                                },
                            },
                        },
                        'scaleAndConcurrency': {
                            'instanceMemoryMB': instance_memory_mb,
                            'maximumInstanceCount': 100,
                        },
                        'runtime': {'name': 'python', 'version': '3.13'},
                    },
                },
            },
        )
        principal_id = result.get('identity', {}).get('principalId')
        log.info(f"Function app '{app_name}' created. Principal: {principal_id}")
        return result

    def set_app_settings(self, resource_group, app_name, settings: dict):
        '''Replace all application settings on a Function App.'''
        self._arm(
            'PUT',
            f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}/config/appsettings',
            json={'properties': settings},
        )
        log.info(f"App settings applied to '{app_name}'.")

    # ------------------------------------------------------------------
    # RBAC
    # ------------------------------------------------------------------

    def _assign_role(self, scope, principal_id, role_id):
        role_def = (
            f'/subscriptions/{self.subscription_id}'
            f'/providers/Microsoft.Authorization/roleDefinitions/{role_id}'
        )
        try:
            self._auth.role_assignments.create(
                scope, str(uuid.uuid4()),
                RoleAssignmentCreateParameters(
                    role_definition_id=role_def,
                    principal_id=principal_id,
                    principal_type='ServicePrincipal',
                ),
            )
        except Exception as e:
            if 'RoleAssignmentExists' in str(e) or 'already exists' in str(e).lower():
                log.info(f"Role '{role_id}' already assigned to '{principal_id}', skipping.")
            else:
                raise

    def assign_data_storage_roles(self, resource_group, storage_account, principal_id):
        '''
        Grant Blob, Queue, and Table Data Contributor roles on a storage account
        to a managed identity principal.
        '''
        scope = (
            f'/subscriptions/{self.subscription_id}'
            f'/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Storage/storageAccounts/{storage_account}'
        )
        for role in (ROLE_BLOB_DATA_CONTRIBUTOR, ROLE_QUEUE_DATA_CONTRIBUTOR, ROLE_TABLE_DATA_CONTRIBUTOR):
            self._assign_role(scope, principal_id, role)
        log.info(f"Storage roles assigned to '{principal_id}' on '{storage_account}'.")

    def assign_keyvault_roles(self, resource_group, keyvault_name, principal_id):
        '''
        Grant Key Vault Secrets User on a Key Vault to a managed identity principal.
        '''
        scope = (
            f'/subscriptions/{self.subscription_id}'
            f'/resourceGroups/{resource_group}'
            f'/providers/Microsoft.KeyVault/vaults/{keyvault_name}'
        )
        self._assign_role(scope, principal_id, ROLE_KEYVAULT_SECRETS_USER)
        log.info(f"Key Vault Secrets User assigned to '{principal_id}' on '{keyvault_name}'.")

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy(self, app_name, function_dir: Path, core_dir: Path):
        '''
        Deploy a Function App using the Azure Functions Core Tools CLI.

        Stages function files and the shared core/ package in a temp directory,
        then runs: func azure functionapp publish <app_name>

        Installs azure-functions-core-tools via npm if func is not found.
        '''
        if not shutil.which('func'):
            log.info('func not found — installing azure-functions-core-tools via npm...')
            result = subprocess.run(
                ['npm', 'install', '-g', 'azure-functions-core-tools@4', '--unsafe-perm', 'true'],
                capture_output=True, text=True,
            )
            if result.returncode != 0 or not shutil.which('func'):
                raise RuntimeError(
                    f'Failed to install azure-functions-core-tools:\n{result.stderr}'
                )

        tmp_dir = tempfile.mkdtemp()
        try:
            shutil.copytree(function_dir, tmp_dir, dirs_exist_ok=True, ignore=_STAGE_IGNORE)
            shutil.copytree(core_dir, Path(tmp_dir) / 'core', dirs_exist_ok=True, ignore=_STAGE_IGNORE)

            result = subprocess.run(
                ['func', 'azure', 'functionapp', 'publish', app_name],
                cwd=tmp_dir,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f'func azure functionapp publish failed for {app_name}')
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        log.info(f'{app_name} deployed.')
