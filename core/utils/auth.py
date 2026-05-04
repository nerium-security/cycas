'''
Module responsible for authenticating and initializing all external service managers.

This module wires together authentication for Azure-based services and
external data sources, including:
    - Azure subscription and credential handling
    - Azure Key Vault
    - Azure Table Storage
    - Azure Queue Storage
    - Azure Blob Storage (account key or SAS-based)
    - Azure Data Explorer (ADX)
    - Optional SFTP access

The main entry point is the `Authenticator` class, which produces an
`AuthManagers` container holding initialized manager instances based
on configuration flags and source selection.
'''

from core.manager.adx import AdxManager
from core.manager.azure import AzureManager
from core.manager.blob import BlobManager
from core.manager.sas import SasManager
from core.manager.sftp import SftpManager
from core.manager.table import TablestorageManager
from core.manager.keyvault import KeyvaultManager
from core.manager.queue import QueueManager
from dataclasses import dataclass
import logging as log

log = log.getLogger(__name__)

@dataclass
class AuthManagers:
    azure:      object
    keyvault:   object
    table:      object
    queue:      object
    adx:        object
    sftp:       object = None
    blob:       object = None
    sas:        object = None


class Authenticator:
    def __init__(self, config) -> AuthManagers:
        '''
        Initialize the Authenticator.

        Reads required configuration values and prepares internal state
        for authenticating all enabled services.

        Args:
            config: Configuration object returned`
        '''

        self.azure_manager = AzureManager()
        self.config = config

        self.blob_logtable_uri          = config.blob_logtable_uri
        self.blob_logtable_name         = config.blob_logtable_name
        self.keyvault_url               = config.keyvault_url
        self.blob_queue_url             = config.blob_queue_url
        self.blob_queue_name            = config.blob_queue_name
        self.blob_storageaccount_uri    = config.blob_storageaccount_uri
        self.blob_storageaccount_sas    = config.blob_storageaccount_sas
        self.sftp_keyvaultsecretname    = config.sftp_keyvaultsecretname
        self.sftp_url                   = config.sftp_url
        self.sftp_username              = config.sftp_username
        self.sftp_port                  = config.sftp_port
        self.adx_cluster_uri            = config.adx_cluster_uri
        self.adx_cluster_ingestion_uri  = config.adx_cluster_ingestion_uri
        self.adx_database_name          = config.adx_database_name
        self.var_verify_enabled             = config.var_verify_enabled
  
    def authenticate_all(self) -> AuthManagers:
        '''
        Authenticate all enabled services and return initialized managers.

        Uses configuration feature flags to determine which services should
        be authenticated. Services that are disabled are returned as False.

        Returns:
            AuthManagers: Container holding all authenticated manager instances.
        '''

        if not self.config.adx_cluster_enabled:
            log.info(f'Skipping authentication to adx cluster: {self.config.adx_cluster_uri}.')

        if not self.config.blob_logtable_enabled:
            log.info(f'Skipping authentication to storage account table: {self.config.blob_logtable_uri}.')

        if not self.config.blob_queue_enabled:
            log.info(f'Skipping authentication to storage account queue: {self.config.blob_queue_url}.')

        if not self.config.keyvault_enabled:
            log.info(f'Skipping authentication to keyvault: {self.config.keyvault_url}.')

        return AuthManagers(
            azure=self.authenticate_azure(),
            keyvault=self.authenticate_keyvault() if self.config.keyvault_enabled else False,
            table=self.authenticate_tablestorage() if self.config.blob_logtable_enabled else False,
            queue=self.authenticate_queue() if self.config.blob_queue_enabled else False,
            adx=self.authenticate_adx() if self.config.adx_cluster_enabled else False,
            sftp=self.authenticate_sftp() if self.config.sftp_enabled else False,
            blob=self.authenticate_blob() if self.config.blob_storageaccount_enabled else False,
            sas=self.authenticate_blob_sas() if self.config.blob_storageaccount_sas_enabled else False
        )

    def authenticate_azure(self):
        '''
        Authenticate to Azure and obtain an Azure credential.

        Uses AzureManager to authenticate and stores the resulting credential
        for reuse by other service managers.
        '''

        self.azure_credential = self.azure_manager.authenticate()

    def authenticate_keyvault(self):
        '''
        Authenticate to Azure Key Vault.

        Initializes a KeyvaultManager and authenticates it using the
        previously acquired Azure credential.

        Returns:
            KeyvaultManager: Authenticated Key Vault manager instance.
        '''    

        keyvault_manager = KeyvaultManager(self.keyvault_url)
        keyvault_manager.authenticate(self.azure_credential, self.var_verify_enabled)

        return keyvault_manager
       
    def authenticate_blob_sas(self):
        '''
        Initialize a SAS-based Blob Storage manager.

        Returns:
            SasManager: Blob manager authenticated using a SAS URL.
        '''

        return SasManager(self.blob_storageaccount_sas)

    def authenticate_blob(self):
        '''
        Authenticate to Azure Blob Storage using Azure credentials.

        Initializes a BlobManager and authenticates it using the Azure
        credential.

        Returns:
            BlobManager: Authenticated Blob Storage manager instance.
        '''

        blob_manager = BlobManager(self.azure_credential, self.blob_storageaccount_uri)
        blob_manager.authenticate()

        return blob_manager

    def authenticate_queue(self):
        '''
        Authenticate to Azure Queue Storage.

        Initializes a QueueManager and authenticates it using the Azure
        credential.

        Returns:
            QueueManager: Authenticated Queue Storage manager instance.
        '''

        queue_manager = QueueManager(self.azure_credential, self.blob_queue_url, self.blob_queue_name)
        queue_manager.authenticate(self.var_verify_enabled)

        return queue_manager

    def authenticate_tablestorage(self):
        '''
        Authenticate to Azure Table Storage.

        Initializes a TablestorageManager and authenticates it using the
        Azure credential.

        Returns:
            TablestorageManager: Authenticated Table Storage manager instance.
        '''

        tablestorage_manager = TablestorageManager(self.azure_credential, self.blob_logtable_uri, self.blob_logtable_name)
        tablestorage_manager.authenticate(self.var_verify_enabled)

        return tablestorage_manager

    def authenticate_sftp(self):
        '''
        Authenticate to an SFTP server using credentials from Azure Key Vault.

        Retrieves the SSH private key from Key Vault, fixes its format if
        necessary, and initializes an SftpManager using key-based
        authentication.

        Returns:
            SftpManager: Authenticated SFTP manager instance.
        '''

        keyvault = self.authenticate_keyvault()
        sftp_sshkey = keyvault.read_creds(self.sftp_keyvaultsecretname)
        sftp_sshkey = keyvault.fix_key_format(sftp_sshkey)
        sftp_manager = SftpManager(
            self.sftp_url,
            self.sftp_username,
            self.sftp_port,
            sftp_sshkey
        )

        sftp_manager.authenticate()

        return sftp_manager

    def authenticate_adx(self):
        '''
        Authenticate to Azure Data Explorer (ADX).

        If cluster configuration is incomplete, prompts the user to
        interactively select a subscription, cluster, and database.
        Initializes and authenticates an AdxManager and runs a test query.

        Returns:
            AdxManager: Authenticated ADX manager instance.

        Notes:
            - This method may prompt for user input.
            - Assumes Azure authentication has already succeeded.
        '''
        
        if not all([self.adx_cluster_uri, 
                    self.adx_cluster_ingestion_uri, 
                    self.adx_database_name]):

            sub = self.azure_manager.select_subscription()
            cluster = self.azure_manager.select_cluster(sub)
            database = self.azure_manager.select_database(cluster)

            self.adx_cluster_uri = cluster.get('uri', '')
            self.adx_cluster_ingestion_uri = cluster.get('data_ingestion_uri', '')
            self.adx_database_name = database.get('name', '')

        adx_manager = AdxManager(self.azure_credential, 
                                self.adx_cluster_uri, 
                                self.adx_cluster_ingestion_uri, 
                                self.adx_database_name)
        
        adx_manager.authenticate(self.var_verify_enabled)

        return adx_manager