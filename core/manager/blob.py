'''
Module for interacting with an Azure Blob Storage account.

Provides a small wrapper around the Azure Blob Storage SDK to
authenticate, list blobs, upload/download blobs, set blob metadata,
create containers, delete blobs, and initiate server-side blob copies.
'''

from azure.storage.blob import BlobServiceClient
from pathlib import Path
import logging as log
import os

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

    def set_metadata(self, container_name, blob_name, metadata):
        '''
        Set metadata on a blob.

        Args:
            container_name (str): Name of the container containing the blob.
            blob_name (str): Name of the blob.
            metadata (dict): Metadata key/value pairs to set.

        '''

        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.set_blob_metadata(metadata)
            log.info(f'Successfully set metadata for blob {blob_name} in container {container_name}')
        except Exception as e:
            log.error(f'Failed to set metadata for blob {blob_name} in container {container_name}. Error: {str(e)}', exc_info=True)

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

    def upload(self, container_name, blob_name, data):
        '''
        Upload data as a blob to the specified container.

        Args:
            container_name (str): Name of the destination container.
            blob_name (str): Name of the blob to create.
            data: Data to upload. Typically bytes, a file-like object,
                or any type accepted by `upload_blob()`.

        Returns:
            bool or None: Returns True if a '.log' blob upload fails and is
            intentionally skipped. Returns False on upload failure for other
            blobs. Returns None on successful upload.

        Notes:
            - The current implementation does not set `overwrite=True`, so
            uploading an existing blob will raise and be treated as a failure.
            - '.log' uploads are treated as optional and may be skipped.
        '''

        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.upload_blob(data)
            log.info(f'Successfully uploaded blob {blob_name} to container {container_name}')
        except Exception as e:
            if blob_name.endswith('.log'):
                log.info(f'Skipped blob upload of {blob_name} to container {container_name}.' )
                return True
            else:
                log.warning(f'Failed to upload blob {blob_name} to container {container_name}. Error: {str(e)}')
                return False

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

        try:
            with open(temp_dir, 'wb') as download_file:
                download_file.write(blob_client.download_blob().readall())

            log.info(f'Successfully downloaded blob {blob_name} from container {container_name} to {download_path}')
            return temp_dir
        except Exception as e:
            
            log.error(f'Failed to download blob {blob_name} from container {container_name}. Error: {str(e)}', exc_info=True)
            return False