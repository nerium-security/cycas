from azure.keyvault.secrets import SecretClient
import logging as log

log = log.getLogger(__name__)

class KeyvaultManager:
    def __init__(self, vault_url):
        self.vault_url = vault_url
        self.credentials = None
        
    def authenticate(self, credential):
        '''Authenticate with Azure Key Vault.'''
        self.credential = credential
        try:
            log.info(f'Authenticating with Azure Key Vault: {self.vault_url}')
            self.client = SecretClient(vault_url=self.vault_url, credential=self.credential)
            log.info('Successfully authenticated.')
            return True
        except Exception as e:
            log.error(f'Authentication with Key Vault failed: {e}')
            return False

    def read_creds(self, secret_name):

        try:
            log.debug(f'Retrieving secret "{secret_name}" from {self.vault_url}')
            secret = self.client.get_secret(secret_name)
            return secret.value
        except Exception as e:
            log.error(f'Failed to retrieve secret "{secret_name}": {e}')
            return None
        
    def fix_key_format(self, key_str: str) -> str:
        '''Fix SFTP key'''
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