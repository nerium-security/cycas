'''
Module for reading secrets from Azure Key Vault.

Provides a small wrapper around the Azure Key Vault Secrets client
to authenticate, retrieve secret values, and perform basic formatting
fixes for secrets such as SSH private keys.
'''

from azure.keyvault.secrets import SecretClient
import logging as log

log = log.getLogger(__name__)

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
        
    def authenticate(self, credential):
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
            
            self.verify_secret_read_permissions()

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