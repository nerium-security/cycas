'''
Module for handling data in blob storage account using authentication through SAS.
'''

import os
import logging as log
from urllib.parse import urlparse
from azure.storage.blob import BlobServiceClient

log = log.getLogger(__name__)

class SasManager:
    def __init__(self, sas_url):
        self.sas_url = sas_url
        self._blob_service_client = None
        self._container_client = None

        if sas_url:
            self._initialize_clients(sas_url)
    
    
    def _initialize_clients(self, sas_url: str) -> None:
        '''
        Initialize the Azure Blob Storage clients from a SAS URL.
        
        Args:
        sas_url (str): The full Shared Access Signature (SAS) URL pointing to
            a specific container in Azure Blob Storage. The URL must include
            both the container path and the SAS token query parameters.
        '''

        parsed_url = urlparse(sas_url)
        self.parsed_url = parsed_url

        self._blob_service_client = BlobServiceClient(
            account_url=f'{parsed_url.scheme}://{parsed_url.netloc}',
            credential=parsed_url.query
        )

        container_name = parsed_url.path.strip('/').split('/')[-1]
        self._container_client = self._blob_service_client.get_container_client(container_name)

        log.debug(f'Initialized BlobServiceClient for container: {container_name}')


    def list_blobs_from_sas(self) -> list[str]:
        '''
        List all blobs from the container represented by a SAS URL.
        '''
        
        log.info(f'Listing blobs from domain: {self.parsed_url.netloc}')

        try:
            if not self._container_client:
                raise ValueError('SAS not initialized. Provide sas_url at init or call _initialize_clients().')

            blobs = [blob.name for blob in self._container_client.list_blobs()]
            log.info(f'Found {len(blobs)} blobs.')
            return blobs

        except Exception as e:
            log.error(f'Error listing blobs: {e}')
            return []
        

    def download(self, download_path: str) -> None:
        '''
        Download all blobs from the container represented by a SAS URL.

        Args:
            download_path (str): Local directory to save blobs.
        '''

        try:
            if not self._container_client:
                raise ValueError('SAS not initialized. Provide sas_url at init or call _initialize_clients().')

            os.makedirs(download_path, exist_ok=True)
            log.info(f'Downloading blobs to {download_path}')

            for blob in self._container_client.list_blobs():
                blob_path = os.path.join(download_path, blob.name)
                os.makedirs(os.path.dirname(blob_path), exist_ok=True)

                with open(blob_path, 'wb') as file:
                    stream = self._container_client.download_blob(blob.name)
                    file.write(stream.readall())

                log.info(f'Downloaded blob: {blob.name}')
                return blob_path
        except Exception as e:
            log.error(f'Error downloading blobs: {e}')