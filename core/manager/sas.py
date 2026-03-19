'''
Module for interacting with an Azure Blob Storage container using a
Shared Access Signature (SAS) for authentication.

This module defines the `SasManager` class, which provides a lightweight
wrapper around the Azure Blob Storage SDK for read-only or limited-access
operations authenticated via a SAS URL.
'''

import os
import logging as log
from urllib.parse import urlparse
from azure.storage.blob import BlobServiceClient

log = log.getLogger(__name__)

class SasManager:
    def __init__(self, sas_url):
        '''
        Initialize the SAS manager with a SAS URL.

        If a SAS URL is provided, Azure Blob Storage clients are initialized
        immediately.

        Args:
            sas_url (str): Full SAS URL pointing to an Azure Blob Storage
                container, including SAS token query parameters.
        '''

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
        List all blobs in the container represented by the SAS URL.

        Returns:
            list[str]: List of blob names found in the container.
            Returns an empty list if the operation fails.
        '''
        
        log.info(f'Listing blobs from domain: {self.parsed_url.netloc}')

        try:

            blobs = [blob.name for blob in self._container_client.list_blobs()]
            log.info(f'Found {len(blobs)} blobs.')
            return blobs

        except Exception as e:
            log.error(f'Error listing blobs: {e}')
            return []

    def download_all(self, download_path: str) -> None:
        '''
        Download all blobs from the container represented by a SAS URL.

        Args:
            download_path (str): Local directory to save blobs.
        '''

        try:
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

    def download(self, download_path: str, zip: str) -> None:
        '''
        Download a single blob from the container to the local filesystem.

        The blob is saved using its name relative to the specified
        download directory.

        Args:
            download_path (str): Local directory where the blob will be saved.
            zip (str): Name of the blob to download.

        Returns:
            str or None: Full local file path if the download succeeds,
            otherwise None.
        '''
        try:
            os.makedirs(download_path, exist_ok=True)

            log.info(f'Downloading blob {zip} to {download_path}')

            blob_path = os.path.join(download_path, zip)
            blob_client = self._container_client.get_blob_client(zip)

            with open(blob_path, 'wb') as file:
                stream = blob_client.download_blob()
                file.write(stream.readall())
            
            log.info(f'Downloaded blob: {blob_path}')

            return blob_path
        except Exception as e:
            log.error(f'Error downloading blob: {e}')