'''
Module for provisioning Azure Web Apps.

Provides WebappManager for creating App Service Plans, Web Apps with
managed identity, and configuring IP access restrictions.
'''

import logging as log
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import requests

from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.authorization.models import RoleAssignmentCreateParameters

log = log.getLogger(__name__)

_ARM_API = '2024-04-01'

_STAGE_IGNORE = shutil.ignore_patterns('.venv', '__pycache__', '.git', '.vscode', 'test')

ROLE_TABLE_DATA_CONTRIBUTOR  = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
ROLE_STORAGE_BLOB_DATA_READER = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'


class WebappManager:
    def __init__(self, credential, subscription_id):
        self.credential      = credential
        self.subscription_id = subscription_id
        self._auth           = AuthorizationManagementClient(credential, subscription_id)

    def _token(self):
        return self.credential.get_token('https://management.azure.com/.default').token

    def _rg_path(self, resource_group):
        return f'/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}'

    def _arm(self, method, path, api_version=None, **kwargs):
        url = f'https://management.azure.com{path}?api-version={api_version or _ARM_API}'
        headers = {
            'Authorization': f'Bearer {self._token()}',
            'Content-Type':  'application/json',
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
                        raise RuntimeError(f'ARM operation {status}: {data.get("error", {})}')

            if method.upper() == 'PUT':
                final = requests.get(
                    url,
                    headers={'Authorization': f'Bearer {self._token()}'},
                    timeout=30,
                )
                return final.json() if final.content else {}

        return resp.json() if resp.content else {}

    def _arm_get(self, path, api_version=None):
        url  = f'https://management.azure.com{path}?api-version={api_version or _ARM_API}'
        resp = requests.get(url, headers={'Authorization': f'Bearer {self._token()}'}, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def provision_plan(self, resource_group, plan_name, location):
        '''Create a Basic B1 Linux App Service Plan.'''
        path     = f'{self._rg_path(resource_group)}/providers/Microsoft.Web/serverfarms/{plan_name}'
        existing = self._arm_get(path)
        if existing:
            log.info(f"App Service Plan '{plan_name}' already exists, skipping creation.")
            return existing
        result = self._arm(
            'PUT', path,
            json={
                'location': location,
                'kind':     'linux',
                'sku':      {'tier': 'Basic', 'name': 'B1'},
                'properties': {'reserved': True},
            },
        )
        log.info(f"Webapp plan '{plan_name}' ready.")
        return result

    def provision_webapp(self, resource_group, app_name, location, plan_id):
        '''Create a Linux Web App with system-assigned managed identity.'''
        path     = f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}'
        existing = self._arm_get(path)
        if existing:
            log.info(f"Web app '{app_name}' already exists, skipping creation.")
            return existing
        result = self._arm(
            'PUT', path,
            json={
                'location': location,
                'kind':     'app,linux',
                'identity': {'type': 'SystemAssigned'},
                'properties': {
                    'serverFarmId': plan_id,
                    'httpsOnly':    True,
                    'reserved':     True,
                    'siteConfig':   {'linuxFxVersion': 'PYTHON|3.13'},
                },
            },
        )
        principal_id = result.get('identity', {}).get('principalId')
        log.info(f"Web app '{app_name}' created. Principal: {principal_id}")
        return result

    def configure_ip_restrictions(self, resource_group, app_name, allowed_ips):
        '''Restrict access to allowed_ips (list of IP or CIDR strings), deny all others.'''
        rules = [
            {
                'name':      f'allow-{i + 1}',
                'ipAddress': ip if '/' in ip else f'{ip}/32',
                'action':    'Allow',
                'priority':  100 + i * 10,
            }
            for i, ip in enumerate(allowed_ips)
        ]
        self._arm(
            'PUT',
            f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}/config/web',
            json={
                'properties': {
                    'ipSecurityRestrictions':              rules,
                    'ipSecurityRestrictionsDefaultAction': 'Deny',
                },
            },
        )
        log.info(f"IP restrictions applied to '{app_name}'.")

    def assign_storage_roles(self, resource_group, account_name, principal_id):
        '''Grant the webapp managed identity Table Data Contributor on the data storage account.'''
        import uuid
        storage_scope = (
            f'/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Storage/storageAccounts/{account_name}'
        )
        role_def = (
            f'/subscriptions/{self.subscription_id}'
            f'/providers/Microsoft.Authorization/roleDefinitions/{ROLE_TABLE_DATA_CONTRIBUTOR}'
        )
        try:
            self._auth.role_assignments.create(
                storage_scope,
                str(uuid.uuid4()),
                RoleAssignmentCreateParameters(
                    role_definition_id=role_def,
                    principal_id=principal_id,
                    principal_type='ServicePrincipal',
                ),
            )
        except Exception as e:
            if 'RoleAssignmentExists' in str(e) or 'already exists' in str(e).lower():
                log.info('Table Data Contributor already assigned, skipping.')
            else:
                raise
        log.info('Table Data Contributor assigned to webapp principal.')

    def assign_blob_container_reader(self, resource_group, account_name, container_name, principal_id):
        '''Grant the webapp managed identity Storage Blob Data Reader on a specific container.'''
        import uuid
        container_scope = (
            f'/subscriptions/{self.subscription_id}/resourceGroups/{resource_group}'
            f'/providers/Microsoft.Storage/storageAccounts/{account_name}'
            f'/blobServices/default/containers/{container_name}'
        )
        role_def = (
            f'/subscriptions/{self.subscription_id}'
            f'/providers/Microsoft.Authorization/roleDefinitions/{ROLE_STORAGE_BLOB_DATA_READER}'
        )
        try:
            self._auth.role_assignments.create(
                container_scope,
                str(uuid.uuid4()),
                RoleAssignmentCreateParameters(
                    role_definition_id=role_def,
                    principal_id=principal_id,
                    principal_type='ServicePrincipal',
                ),
            )
        except Exception as e:
            if 'RoleAssignmentExists' in str(e) or 'already exists' in str(e).lower():
                log.info(f'Blob Data Reader on {container_name} already assigned, skipping.')
            else:
                raise
        log.info(f'Blob Data Reader assigned on container {container_name!r} to webapp principal.')

    def configure_startup(self, resource_group, app_name, command: str):
        '''Set the startup command on the Web App without overwriting other site config.'''
        self._arm(
            'PATCH',
            f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}',
            json={'properties': {'siteConfig': {'appCommandLine': command}}},
        )
        log.info(f"Startup command set on '{app_name}': {command}")

    def set_app_settings(self, resource_group, app_name, settings: dict):
        '''Replace all application settings on the Web App.'''
        self._arm(
            'PUT',
            f'{self._rg_path(resource_group)}/providers/Microsoft.Web/sites/{app_name}/config/appsettings',
            json={'properties': settings},
        )
        log.info(f"App settings applied to '{app_name}'.")

    def deploy(self, resource_group, app_name, webapp_dir: Path, core_dir: Path):
        '''
        Deploy the webapp via Kudu ZIP deploy.

        Stages webapp/ and core/ into a temp directory, creates a ZIP, and
        POSTs it to the Kudu zipdeploy endpoint. App Service extracts the ZIP
        to /home/site/wwwroot, giving the layout expected by startup.txt:
          wwwroot/webapp/app.py
          wwwroot/core/...
        '''
        import tomllib

        tmp_dir  = Path(tempfile.mkdtemp())
        zip_path = tmp_dir.parent / f'{app_name}-deploy.zip'
        try:
            shutil.copytree(webapp_dir, tmp_dir / 'webapp', ignore=_STAGE_IGNORE)
            shutil.copytree(core_dir,   tmp_dir / 'core',   ignore=_STAGE_IGNORE)

            pyproject = tomllib.loads((webapp_dir.parent / 'pyproject.toml').read_text())
            deps      = pyproject.get('project', {}).get('dependencies', [])
            (tmp_dir / 'requirements.txt').write_text('\n'.join(deps) + '\n')

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file in tmp_dir.rglob('*'):
                    if file.is_file():
                        zf.write(file, file.relative_to(tmp_dir))

            kudu_url = f'https://{app_name}.scm.azurewebsites.net/api/zipdeploy'
            log.info(f"Deploying to '{app_name}' via Kudu ZIP deploy...")
            with open(zip_path, 'rb') as f:
                resp = requests.post(
                    kudu_url,
                    headers={
                        'Authorization': f'Bearer {self._token()}',
                        'Content-Type':  'application/zip',
                    },
                    data=f,
                    timeout=300,
                )
            resp.raise_for_status()
            log.info(f"'{app_name}' deployed successfully.")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            zip_path.unlink(missing_ok=True)
