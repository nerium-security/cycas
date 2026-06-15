'''
Module for interacting with an Azure Blob Storage account.

Provides a small wrapper around the Azure Blob Storage SDK to
authenticate, list blobs, upload/download blobs, set blob metadata,
create containers, delete blobs, and initiate server-side blob copies.
'''

from azure.storage.blob import BlobServiceClient
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.storage.models import StorageAccountCreateParameters, Sku, Kind
from pathlib import Path
import logging as log
import os
import sys

log = log.getLogger(__name__)

class BlobManager:
    def __init__(self, credential, account_url):
        '''
        Initialize the BlobManager.

        Args:
            credential: Azure credential used to authenticate with Blob Storage.
                This can be an Azure Identity credential (e.g. DefaultAzureCredential)
                or a shared key/SAS token supported by BlobServiceClient.
            account_url (str): Storage account URL (e.g. 'https://<account>.blob.core.windows.net').
        '''
        self.credential = credential
        self.account_url = account_url

    def authenticate(self):
        '''
        Authenticate to Azure Blob Storage and initialize the service client.

        Creates a `BlobServiceClient` using the provided account URL and
        credential. Errors are logged and not raised.
        '''

        log.info(f'Attempting to authenticate with blob storage account: {self.account_url}')
        try:

            self.blob_service_client = BlobServiceClient(account_url=self.account_url, credential=self.credential)
            log.info(f'Successfully authenticated.')
        except Exception as e:
            log.error(f'Could not authenticated. Error: {e}')

    def get_container_client(self, container_name):
        '''
        Get a container client for a given container.

        Args:
            container_name (str): Name of the container.

        Returns:
            ContainerClient: Azure Blob Storage container client.
        '''

        return self.blob_service_client.get_container_client(container_name)

    def get_client(self, container_name, blob_name):
        '''
        Get a blob client for a given blob.

        Args:
            container_name (str): Name of the container.
            blob_name (str): Name of the blob.

        Returns:
            BlobClient: Azure Blob Storage blob client.
        '''

        return self.blob_service_client.get_blob_client(container=container_name, blob=blob_name)

    def list_blobs(self, container_name):
        '''
        List all blobs in the specified container.

        Args:
            container_name (str): Name of the container to list.

        Returns:
            list[str]: List of blob names. Returns an empty list on failure.
        '''
        
        try:
            container_client = self.get_container_client(container_name)
            
            blobs = container_client.list_blobs()
            details = self.account_url + '/' + container_name
            log.info(f'Listing blobs in: {details}')
            blob_names = []
            for blob in blobs:
                blob_names.append(blob.name)
                log.debug(f'Blob found: {blob.name}')

            if not blob_names:
                log.error('No blob found.')
            return blob_names
        except Exception as e:
            log.error(
                f'Failed to list blobs in container {container_name}. '
                'Try adding Storage Blob Data Contributor as a role to the storage account. '
                'Exiting script.'
            )
            log.debug(f'Error: {str(e)}', exc_info=True)
            sys.exit(1)

    def list_blobs_with_sizes(self, container_name) -> list[tuple[str, int]]:
        '''
        List all blobs in a container together with their sizes in bytes.

        Returns:
            list[tuple[str, int]]: List of (blob_name, size_bytes) pairs.
        '''
        try:
            container_client = self.get_container_client(container_name)
            result = []
            for blob in container_client.list_blobs():
                result.append((blob.name, blob.size or 0))
                log.debug(f'Blob found: {blob.name} ({blob.size} bytes)')
            if not result:
                log.error('No blob found.')
            return result
        except Exception as e:
            log.error(
                f'Failed to list blobs in container {container_name}. '
                'Try adding Storage Blob Data Contributor as a role to the storage account. '
                'Exiting script.'
            )
            log.debug(f'Error: {str(e)}', exc_info=True)
            sys.exit(1)
        except Exception as e:
            log.error(  
                f'Failed to list blobs in container {container_name}. '
                'Try adding Storage Blob Data Contributor as a role to the storage account. '
                'Exiting script.'
            )
            log.debug(f'Error: {str(e)}', exc_info=True)
            sys.exit(1)

    def create_container(self, container_name):
        '''
        Create a container if possible.

        Attempts to create a container and logs success. Exceptions are
        silently ignored.

        Args:
            container_name (str): Name of the container to create.
        '''

        container_client = self.get_container_client(container_name)
        try:
            container_client.create_container()
            log.info(f'Successfully created container {container_name}')         
        except:
            pass

    def delete(self, container_name, blob_name):
        '''
        Delete a blob from a container.

        Args:
            container_name (str): Name of the container containing the blob.
            blob_name (str): Name of the blob to delete.

        Returns:
            bool: True if deletion succeeds, otherwise False.
        '''

        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.delete_blob()
            log.info(f'Successfully deleted blob {blob_name} from container {container_name}')
            return True
        except Exception as e:
            log.error(f'Failed to delete blob {blob_name} from container {container_name}. Error: {str(e)}', exc_info=True)
            return False

    def copy(self, source_container_name, destination_container_name, source_blob_name, destination_blob_name):
        '''
        Initiate a server-side copy of a blob.

        Starts an asynchronous copy operation from the source blob URL into
        the destination blob.

        Args:
            source_container_name (str): Source container name.
            destination_container_name (str): Destination container name.
            source_blob_name (str): Source blob name.
            destination_blob_name (str): Destination blob name.

        Returns:
            bool: True if the copy operation was initiated successfully,
            otherwise False.
        '''

        source_blob_client = self.get_client(source_container_name, source_blob_name)
        destination_blob_client = self.get_client(destination_container_name, destination_blob_name)
        
        try:
            source_blob_url = source_blob_client.url
            copy_operation = destination_blob_client.start_copy_from_url(source_blob_url)
            
            log.info('Copy operation status: %s', copy_operation['copy_status'])
            log.info(f'Successfully initiated copy of blob {source_blob_name} from container {source_container_name} to blob {destination_blob_name} in container {destination_container_name}')
            return True
        except Exception as e:
            log.error(f'Failed to copy blob {source_blob_name} from container {source_container_name} to blob {destination_blob_name} in container {destination_container_name}. Error: {str(e)}', exc_info=True)
            return False

    def download(self, container_name, blob_name, download_path):
        '''
        Download a blob to the local filesystem.

        Writes the blob contents to a local file under `download_path` using
        the blob name as the filename.

        Args:
            container_name (str): Name of the container containing the blob.
            blob_name (str): Name of the blob to download.
            download_path (str): Local directory where the blob will be saved.

        Returns:
            str or bool: Full local file path on success, otherwise False.
        '''

        blob_client = self.get_client(container_name, blob_name)
        
        temp_dir = Path(os.path.join(download_path, blob_name))
        temp_dir.parent.mkdir(parents=True, exist_ok=True)
        log.info(f'Starting to download blob "{blob_name}" from storage account.')
        try:
            with open(temp_dir, 'wb') as download_file:
                blob_client.download_blob(max_concurrency=4).readinto(download_file)

            log.info(f'Successfully downloaded blob {blob_name} from container {container_name} to {download_path}')
            return str(temp_dir)
        except Exception as e:
            
            log.error(f'Failed to download blob {blob_name} from container {container_name}. Error: {str(e)}', exc_info=True)
            return False

    def upload_json(self, container_name, blob_name, data):
        '''
        Serialize a dict to JSON and upload it as a blob.

        Datetimes are converted to ISO-8601 strings. Existing blobs are
        overwritten so re-runs always reflect the latest pipeline output.

        Args:
            container_name (str): Target container name.
            blob_name (str): Blob name (e.g. '<uploadid>.json').
            data (dict): Data to serialize and upload.
        '''
        import json
        from datetime import datetime

        def _default(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f'Object of type {type(obj).__name__} is not JSON serializable')

        payload = json.dumps(data, default=_default, indent=2).encode('utf-8')
        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.upload_blob(payload, overwrite=True)
            log.info(f'Uploaded status JSON to {container_name}/{blob_name}')
        except Exception as e:
            log.error(f'Failed to upload status JSON to {container_name}/{blob_name}. Error: {e}')

    def list_blobs_prefix(self, container_name, prefix):
        '''Return blob names in container whose names start with prefix.'''
        try:
            container_client = self.get_container_client(container_name)
            return [b.name for b in container_client.list_blobs(name_starts_with=prefix)]
        except Exception as e:
            log.warning(f'Could not list blobs in {container_name} with prefix {prefix!r}: {e}')
            return []

    def upload_file(self, container_name, blob_name, file_path):
        '''Upload a local file as a blob, overwriting any existing content.'''
        blob_client = self.get_client(container_name, blob_name)
        try:
            with open(file_path, 'rb') as f:
                blob_client.upload_blob(f, overwrite=True)
            log.info(f'Uploaded {file_path} to {container_name}/{blob_name}')
        except Exception as e:
            log.error(f'Failed to upload {file_path} to {container_name}/{blob_name}: {e}')

    def upload_text(self, container_name, blob_name, text):
        '''Upload a UTF-8 string as a blob, overwriting any existing content.'''
        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.upload_blob(text.encode('utf-8'), overwrite=True)
            log.info(f'Uploaded text blob to {container_name}/{blob_name}')
        except Exception as e:
            log.error(f'Failed to upload text blob to {container_name}/{blob_name}: {e}')

    def read_text(self, container_name, blob_name):
        '''Download a blob and return its content as a UTF-8 string, or None if not found.'''
        from azure.core.exceptions import ResourceNotFoundError
        blob_client = self.get_client(container_name, blob_name)
        try:
            return blob_client.download_blob().readall().decode('utf-8')
        except ResourceNotFoundError:
            log.info(f'Blob {container_name}/{blob_name} not found.')
            return None
        except Exception as e:
            log.warning(f'Could not read {container_name}/{blob_name}: {e}')
            return None

    def read_json(self, container_name, blob_name):
        '''Download a blob and parse it as JSON. Returns the dict, or None if the blob
        does not exist or cannot be read.'''
        import json
        from azure.core.exceptions import ResourceNotFoundError
        blob_client = self.get_client(container_name, blob_name)
        try:
            data = blob_client.download_blob().readall()
            return json.loads(data)
        except ResourceNotFoundError:
            log.info(f'Blob {container_name}/{blob_name} not found.')
            return None
        except Exception as e:
            log.warning(f'Could not read {container_name}/{blob_name}: {e}')
            return None

    def generate_container_sas_url(self, subscription_id, resource_group, account_name,
                                    container_name, expiry_days=30):
        '''Generate a time-limited, write-only SAS URL for a container.

        Useful for handing upload access to untrusted endpoints (e.g. an
        offline Velociraptor collector) without exposing the account key
        or granting read/delete access.

        Returns:
            tuple[str, datetime]: The SAS URL and its UTC expiry.
        '''
        from datetime import datetime, timedelta, timezone
        from azure.storage.blob import generate_container_sas, ContainerSasPermissions

        mgmt = StorageManagementClient(self.credential, subscription_id)
        keys = mgmt.storage_accounts.list_keys(resource_group, account_name)
        account_key = keys.keys[0].value

        expiry = datetime.now(timezone.utc) + timedelta(days=expiry_days)
        token = generate_container_sas(
            account_name=account_name,
            container_name=container_name,
            account_key=account_key,
            permission=ContainerSasPermissions(write=True, create=True),
            expiry=expiry,
        )
        sas_url = f'{self.account_url}/{container_name}?{token}'
        log.info(f"Generated write-only SAS URL for container '{container_name}' (expires {expiry.isoformat()}).")
        return sas_url, expiry

    def switch_to_account_key(self, subscription_id, resource_group, account_name):
        '''Re-initialise the blob service client using the storage account key.

        Useful during provisioning when the caller has ARM control-plane access
        but the data-plane RBAC role has not yet been assigned or propagated.
        '''
        from azure.mgmt.storage import StorageManagementClient
        mgmt = StorageManagementClient(self.credential, subscription_id)
        keys = mgmt.storage_accounts.list_keys(resource_group, account_name)
        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=keys.keys[0].value,
        )
        log.info(f"Switched blob client to account-key auth for '{account_name}'.")

    def provision_storage_account(self, subscription_id, resource_group, location, account_name):
        '''
        Create a storage account if it does not already exist.

        Args:
            subscription_id (str): Azure subscription ID.
            resource_group (str): Resource group name.
            location (str): Azure region.
            account_name (str): Storage account name (3-24 lowercase alphanumeric).

        Returns:
            StorageAccount: The created or existing storage account object.
        '''

        mgmt = StorageManagementClient(self.credential, subscription_id)

        try:
            existing = mgmt.storage_accounts.get_properties(resource_group, account_name)
            log.info(f"Storage account '{account_name}' already exists, skipping creation.")
            return existing
        except Exception:
            pass

        log.info(f"Provisioning storage account '{account_name}'...")
        result = mgmt.storage_accounts.begin_create(
            resource_group,
            account_name,
            StorageAccountCreateParameters(
                sku=Sku(name='Standard_LRS'),
                kind=Kind.STORAGE_V2,
                location=location,
                allow_blob_public_access=False,
            )
        ).result()
        log.info(f"Storage account '{account_name}' ready.")
        return result