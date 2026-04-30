'''
Module for reading secrets from Azure Key Vault.

Provides a small wrapper around the Azure Key Vault Secrets client
to authenticate, retrieve secret values, and perform basic formatting
fixes for secrets such as SSH private keys.
'''

import base64
import json
import time
import uuid
import requests
from azure.keyvault.secrets import SecretClient
import logging as log

log = log.getLogger(__name__)

_ARM_API = '2023-07-01'
_AUTH_API = '2022-04-01'
ROLE_SECRETS_OFFICER = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

class KeyvaultManager:
    def __init__(self, vault_url):
        '''
        Initialize the KeyvaultManager.

        Args:
            vault_url (str): URL of the Azure Key Vault
                (e.g. 'https://<vault-name>.vault.azure.net/').
        '''

        self.vault_url = vault_url
        self.credentials = None
        
    @staticmethod
    def _arm_token(credential):
        return credential.get_token('https://management.azure.com/.default').token

    @staticmethod
    def _tenant_id(credential):
        token = KeyvaultManager._arm_token(credential)
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.b64decode(payload))['tid']

    @staticmethod
    def provision_vault(credential, subscription_id, resource_group, vault_name, location):
        '''
        Create a Key Vault with RBAC authorisation if it does not exist.

        Returns:
            str: The vault URI (e.g. https://<name>.vault.azure.net/).
        '''
        token = KeyvaultManager._arm_token(credential)
        url = (
            f'https://management.azure.com/subscriptions/{subscription_id}'
            f'/resourceGroups/{resource_group}'
            f'/providers/Microsoft.KeyVault/vaults/{vault_name}'
            f'?api-version={_ARM_API}'
        )
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.ok:
            log.info(f"Key Vault '{vault_name}' already exists.")
            return resp.json()['properties']['vaultUri']

        body = {
            'location': location,
            'properties': {
                'sku': {'family': 'A', 'name': 'standard'},
                'tenantId': KeyvaultManager._tenant_id(credential),
                'enableRbacAuthorization': True,
                'softDeleteRetentionInDays': 7,
            },
        }
        resp = requests.put(url, headers=headers, json=body, timeout=120)
        if resp.status_code == 409:
            log.info(f"Key Vault '{vault_name}' already exists (409 conflict), using existing.")
            return f'https://{vault_name}.vault.azure.net/'
        resp.raise_for_status()
        vault_uri = (
            resp.json().get('properties', {}).get('vaultUri')
            or f'https://{vault_name}.vault.azure.net/'
        )
        log.info(f"Key Vault '{vault_name}' created.")
        return vault_uri

    @staticmethod
    def assign_role(credential, subscription_id, resource_group, vault_name, principal_id, role_id):
        '''Grant an RBAC role on the Key Vault to a principal.'''
        scope = (
            f'/subscriptions/{subscription_id}'
            f'/resourceGroups/{resource_group}'
            f'/providers/Microsoft.KeyVault/vaults/{vault_name}'
        )
        assignment_id = str(uuid.uuid4())
        url = (
            f'https://management.azure.com{scope}'
            f'/providers/Microsoft.Authorization/roleAssignments/{assignment_id}'
            f'?api-version={_AUTH_API}'
        )
        body = {
            'properties': {
                'roleDefinitionId': (
                    f'/subscriptions/{subscription_id}'
                    f'/providers/Microsoft.Authorization/roleDefinitions/{role_id}'
                ),
                'principalId': principal_id,
            }
        }
        token = KeyvaultManager._arm_token(credential)
        resp = requests.put(
            url,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json=body,
            timeout=30,
        )
        if resp.status_code == 409 or 'already exists' in resp.text.lower():
            log.info('Key Vault role already assigned.')
            return
        resp.raise_for_status()
        log.info(f"Role '{role_id}' assigned to '{principal_id}' on '{vault_name}'.")

    def set_secret(self, secret_name, secret_value, retries=8, delay=15):
        '''
        Upload a secret, retrying to absorb RBAC propagation delay.

        Requires authenticate() to have been called first.
        '''
        for attempt in range(1, retries + 1):
            try:
                self.client.set_secret(secret_name, secret_value)
                log.info(f"Secret '{secret_name}' uploaded.")
                return
            except Exception as e:
                if attempt == retries:
                    raise
                log.warning(
                    f"Secret upload attempt {attempt}/{retries} failed "
                    f"(RBAC propagation?), retrying in {delay}s: {e}"
                )
                time.sleep(delay)

    def authenticate(self, credential, verify_enabled):
        '''
        Authenticate with Azure Key Vault.

        Initializes a SecretClient using the provided Azure credential.

        Args:
            credential: Azure credential used to authenticate with
                Azure Key Vault (e.g. DefaultAzureCredential).

        Returns:
            bool: True if authentication succeeds, otherwise False.
        '''

        self.credential = credential
        try:
            log.info(f'Authenticating with Azure Key Vault: {self.vault_url}')
            self.client = SecretClient(vault_url=self.vault_url, credential=self.credential)
            log.info('Successfully authenticated.')
            if verify_enabled:
                self.verify_secret_read_permissions()
            else:
                log.info('Skipping key vault read as configured in .env with VAR_VERIFY_ENABLED.')

            return True
        except Exception as e:
            log.error(f'Authentication with Key Vault failed: {e}')
            return False

    def verify_secret_read_permissions(self):
        '''
        Verify that the authenticated credential has permission to read secrets
        from Azure Key Vault.
        
        Attempts to list secrets in the vault to confirm read access. This is a
        lightweight permission check that does not retrieve secret values.
        
        Returns:
            bool: True if read permissions are confirmed, otherwise False.
        '''

        try:
            log.info(f'Verifying secret read permissions for vault: {self.vault_url}')
            
            # Attempt to list secrets — requires 'secrets/list' permission.
            secrets = self.client.list_properties_of_secrets()
            next(iter(secrets), None)
            
            log.info('Secret read permissions verified successfully.')
            return True
            
        except HttpResponseError as e:
            if e.status_code == 403:
                log.error(
                    f'Permission denied: credential lacks secret read access '
                    f'to vault "{self.vault_url}". '
                    f'Ensure the identity has a Key Vault Secrets User role or '
                    f'an access policy granting "list" and "get" permissions. '
                    f'Details: {e.message}'
                )
            else:
                log.error(f'HTTP error while verifying permissions: {e}')
            return False


    def read_creds(self, secret_name):
        '''
        Retrieve a secret value from Azure Key Vault.

        Args:
            secret_name (str): Name of the secret to retrieve.

        Returns:
            str or None: Secret value if retrieval succeeds,
            otherwise None.
        '''

        try:
            log.debug(f'Retrieving secret "{secret_name}" from {self.vault_url}')
            secret = self.client.get_secret(secret_name)
            return secret.value
        except Exception as e:
            log.error(f'Failed to retrieve secret "{secret_name}": {e}')
            return None
        
    def fix_key_format(self, key_str: str) -> str:
        '''
        Normalize the format of an RSA private key string.

        Ensures the key is formatted as a valid PEM block by inserting
        missing newlines and wrapping the key body to 64-character lines
        when necessary. This is primarily intended for SSH private keys
        retrieved from secret stores.

        Args:
            key_str (str): RSA private key as a single-line or improperly
                formatted string.

        Returns:
            str: PEM-formatted RSA private key string.
        '''

        # Insert newlines where expected (if needed)
        if '-----BEGIN RSA PRIVATE KEY-----' in key_str and '\n' not in key_str:
            key_str = key_str.replace('-----BEGIN RSA PRIVATE KEY-----', '-----BEGIN RSA PRIVATE KEY-----\n')
            key_str = key_str.replace('-----END RSA PRIVATE KEY-----', '\n-----END RSA PRIVATE KEY-----')
            # Split content between header and footer
            parts = key_str.split('\n')
            if len(parts) == 3:
                header, body, footer = parts
                # Re-wrap key body to 64-char lines (typical PEM format)
                import textwrap
                wrapped_body = '\n'.join(textwrap.wrap(body.strip(), 64))
                key_str = f'{header}\n{wrapped_body}\n{footer}'
        return key_str