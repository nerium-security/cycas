from ..manager.adx import AdxManager
from ..manager.azure import AzureManager
from ..manager.blob import BlobManager
from ..manager.sas import SasManager
from ..manager.sftp import SftpManager
from ..manager.table import TablestorageManager
from ..manager.keyvault import KeyvaultManager
from ..manager.queue import QueueManager
from dataclasses import dataclass
from ..utils.config import load_config
import logging as log

Config = load_config()

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
    def __init__(self, config, source) -> AuthManagers:

        self.azure_manager = AzureManager()
        self.config = config
        self.source = source

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
  
    def authenticate_all(self) -> AuthManagers:

        if not Config.adx_cluster_enabled:
            log.info(f'Skipping authentication to adx cluster: {Config.adx_cluster_uri}.')

        if not Config.blob_logtable_enabled:
            log.info(f'Skipping authentication to storage account table: {Config.blob_logtable_uri}.')

        if not Config.blob_queue_enabled:
            log.info(f'Skipping authentication to storage account queue: {Config.blob_queue_url}.')

        if not Config.keyvault_enabled:
            log.info(f'Skipping authentication to keyvault: {Config.keyvault_url}.')

        return AuthManagers(
            azure=self.authenticate_azure(),
            keyvault=self.authenticate_keyvault() if Config.keyvault_enabled else False,
            table=self.authenticate_tablestorage() if Config.blob_logtable_enabled else False,
            queue=self.authenticate_queue() if Config.blob_queue_enabled else False,
            adx=self.authenticate_adx() if Config.adx_cluster_enabled else False,
            sftp=self.authenticate_sftp() if Config.sftp_enabled else False,
            blob=self.authenticate_blob() if Config.blob_storageaccount_enabled else False,
            sas=self.authenticate_blob_sas() if self.source == 'sas' else False
        )

    def authenticate_azure(self):

        self.azure_credential = self.azure_manager.authenticate()

    def authenticate_keyvault(self):
        
        keyvault_manager = KeyvaultManager(self.keyvault_url)
        keyvault_manager.authenticate(self.azure_credential)

        return keyvault_manager
       
    def authenticate_blob_sas(self):
        
        return SasManager(self.blob_storageaccount_sas)

    def authenticate_blob(self):

        blob_manager = BlobManager(self.azure_credential, self.blob_storageaccount_uri)
        blob_manager.authenticate()

        return blob_manager

    def authenticate_queue(self):

        queue_manager = QueueManager(self.azure_credential, self.blob_queue_url, self.blob_queue_name)
        queue_manager.authenticate()

        return queue_manager

    def authenticate_tablestorage(self):
        
        tablestorage_manager = TablestorageManager(self.azure_credential, self.blob_logtable_uri, self.blob_logtable_name)
        tablestorage_manager.authenticate()

        return tablestorage_manager

    def authenticate_sftp(self):

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
        
        adx_manager.authenticate()

        adx_manager.query_db_test()

        return adx_manager