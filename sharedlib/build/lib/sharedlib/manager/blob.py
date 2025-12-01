'''
Module for handling data in blob storage account.
'''

from azure.storage.blob import BlobServiceClient
import logging as log
import os

log = log.getLogger(__name__)

class BlobManager:
    def __init__(self, credential, account_url):
        self.credential = credential
        self.account_url = account_url

    def authenticate(self):

        log.info(f'Attempting to authenticate with blob storage account: {self.account_url}')
        try:

            self.blob_service_client = BlobServiceClient(account_url=self.account_url, credential=self.credential)
            log.info(f'Successfully authenticated.')
        except Exception as e:
            log.error(f'Could not authenticated. Error: {e}')

    def get_container_client(self, container_name):
        return self.blob_service_client.get_container_client(container_name)

    def get_client(self, container_name, blob_name):
        return self.blob_service_client.get_blob_client(container=container_name, blob=blob_name)

    def list_blobs(self, container_name):
        '''List all blobs in the specified container.'''
        
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
            log.error(f'Failed to list blobs in container {container_name}. Error: {str(e)}', exc_info=True)
            return []

    def set_metadata(self, container_name, blob_name, metadata):
        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.set_blob_metadata(metadata)
            log.info(f'Successfully set metadata for blob {blob_name} in container {container_name}')
        except Exception as e:
            log.error(f'Failed to set metadata for blob {blob_name} in container {container_name}. Error: {str(e)}', exc_info=True)

    def create_container(self, container_name):
        container_client = self.get_container_client(container_name)
        try:
            container_client.create_container()
            log.info(f'Successfully created container {container_name}')         
        except Exception as e:
            pass

    def upload(self, container_name, blob_name, data):
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

        blob_client = self.get_client(container_name, blob_name)
        try:
            blob_client.delete_blob()
            log.info(f'Successfully deleted blob {blob_name} from container {container_name}')
            return True
        except Exception as e:
            log.error(f'Failed to delete blob {blob_name} from container {container_name}. Error: {str(e)}', exc_info=True)
            return False

    def copy(self, source_container_name, destination_container_name, source_blob_name, destination_blob_name):
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

        blob_client = self.get_client(container_name, blob_name)
        
        temp_dir = os.path.join(download_path, blob_name)

        try:
            with open(temp_dir, 'wb') as download_file:
                download_file.write(blob_client.download_blob().readall())

            log.info(f'Successfully downloaded blob {blob_name} from container {container_name} to {download_path}')
            return temp_dir
        except Exception as e:
            log.error(f'Failed to download blob {blob_name} from container {container_name}. Error: {str(e)}', exc_info=True)
            return False