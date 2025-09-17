from sharedlib.manager import AdxManager, AzureManager, BlobManager, SftpManager, TablestorageManager, KeyvaultManager, QueueManager
from dataclasses import dataclass
import logging as log

log = log.getLogger(__name__)

@dataclass
class AuthManagers:
    azure: object
    keyvault: object
    table: object
    queue: object
    adx: object
    sftp: object = None
    blob: object = None

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
        self.sftp_keyvaultsecretname    = config.sftp_keyvaultsecretname
        self.sftp_url                   = config.sftp_url
        self.sftp_username              = config.sftp_username
        self.sftp_port                  = config.sftp_port
        self.adx_cluster_uri            = config.adx_cluster_uri
        self.adx_cluster_ingestion_uri  = config.adx_cluster_ingestion_uri
        self.adx_database_name          = config.adx_database_name
  
    def authenticate_all(self) -> AuthManagers:
        return AuthManagers(
            azure=self.authenticate_azure(),
            keyvault=self.authenticate_keyvault(),
            table=self.authenticate_tablestorage(),
            queue=self.authenticate_queue(),
            adx=self.authenticate_adx(),
            sftp=self.authenticate_sftp() if self.source == 'sftp' else None,
            blob=self.authenticate_blob() if self.source == 'blob' else None
        )

    def authenticate_azure(self):

        self.azure_credential = self.azure_manager.authenticate()

    def authenticate_keyvault(self):
        
        keyvault_manager = KeyvaultManager(self.keyvault_url)
        keyvault_manager.authenticate(self.azure_credential)

        return keyvault_manager
       
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