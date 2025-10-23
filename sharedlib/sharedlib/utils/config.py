import os
from dotenv import load_dotenv, find_dotenv
from dataclasses import dataclass
import logging as log

log = log.getLogger(__name__)

@dataclass
class Config:
    blob_storageaccount_enabled: bool
    blob_storageaccount_uri: str
    blob_storageaccount_sas: str
    blob_container_input: str
    blob_logtable_enabled: bool
    blob_logtable_uri: str
    blob_logtable_name: str
    blob_queue_enabled: bool
    blob_queue_url: str
    blob_queue_name: str
    adx_cluster_enabled: bool
    adx_database_name: str
    adx_cluster_uri: str
    adx_cluster_ingestion_uri: str
    keyvault_enabled: bool
    keyvault_url: str
    keyvault_passwordlocation: str
    sftp_enabled: bool
    sftp_keyvaultsecretname: str
    sftp_username: str
    sftp_url: str
    sftp_port: int
    var_localfolder_directory: str
    var_unzip_directory: str
    var_sample_size: int
    var_df_chunksize: int
    var_download_directory: str
    var_loglocation: str
    var_webhook_url: str
    var_removezip: str
    var_zipfile_prefix: str
    var_zipfile_suffix: str
    var_location_ignorelist: str
    var_retryfailed: bool
    var_verifyuploads: str
    var_loglevel: str
    var_delete_processedzipfiles: str
    var_localdevice_concurrency: int
    var_add_hostname: bool
    velociraptor_enabled: bool
    velociraptor_url: str
    velociraptor_remappingdir: str
    velociraptor_binary: str
    velociraptor_definitions: str
    velociraptor_outputformat: str
    velociraptor_artifactslist: str
    velociraptor_postprocess: str
    
def str_to_bool(value: str) -> bool:
    return str(value).strip().lower() in ('true', 'True', 'TRUE', '1', 'yes')

def load_config() -> Config:

    log.info('Loading .env file.')
    try:
        load_dotenv(find_dotenv())
        log.info('Successfully loaded .env file.')
    except Exception as e:
        log.error(f'Could not load .env file: {e}')

    return Config(
        blob_storageaccount_uri		    = os.getenv('BLOB_STORAGEACCOUNT_URI'),
        blob_storageaccount_sas         = os.getenv('BLOB_STORAGEACCOUNT_SAS'),
        blob_container_input		    = os.getenv('BLOB_CONTAINER_INPUT'),
        blob_logtable_uri			    = os.getenv('BLOB_LOGTABLE_URI'),
        blob_logtable_name			    = os.getenv('BLOB_LOGTABLE_NAME'),
        blob_queue_url			 	    = os.getenv('BLOB_QUEUE_URL'),
        blob_queue_name			 	    = os.getenv('BLOB_QUEUE_NAME'),
        adx_database_name			    = os.getenv('ADX_DATABASE_NAME'),
        adx_cluster_uri			 	    = os.getenv('ADX_CLUSTER_URI'),
        adx_cluster_ingestion_uri	    = os.getenv('ADX_CLUSTER_INGESTION_URI'),
        keyvault_url			 	    = os.getenv('KEYVAULT_URL'),
        keyvault_passwordlocation       = os.getenv('KEYVAULT_PASSWORDLOCATION'),
        sftp_keyvaultsecretname		    = os.getenv('SFTP_KEYVAULTSECRETNAME'),
        sftp_username			 	    = os.getenv('SFTP_USERNAME'),
        sftp_url			 		    = os.getenv('SFTP_URL'),
        var_download_directory          = os.getenv('vAR_DOWNLOAD_DIRECTORY'),
        var_localfolder_directory       = os.getenv('VAR_LOCALFOLDER_DIRECTORY'),
        var_unzip_directory			    = os.getenv('VAR_UNZIP_DIRECTORY'),
        var_loglocation			 	    = os.getenv('VAR_LOGLOCATION'),
        var_webhook_url			 	    = os.getenv('VAR_WEBHOOK_URL'),
        var_zipfile_prefix			    = os.getenv('VAR_ZIPFILE_PREFIX'),
        var_zipfile_suffix			    = os.getenv('VAR_ZIPFILE_SUFFIX'),
        var_location_ignorelist		    = os.getenv('VAR_LOCATION_IGNORELIST'),
        var_loglevel                    = os.getenv('VAR_LOGLEVEL'),
        velociraptor_url                = os.getenv('VELOCIRAPTOR_URL'),
        velociraptor_binary             = os.getenv('VELOCIRAPTOR_BINARY'),
        velociraptor_remappingdir       = os.getenv('VELOCIRAPTOR_REMAPPINGDIR'),
        velociraptor_artifactslist      = os.getenv('VELOCIRAPTOR_ARTIFACTSLIST'),
        velociraptor_definitions        = os.getenv('VELOCIRAPTOR_DEFINITIONS'),
        velociraptor_outputformat       = os.getenv('VELOCIRAPTOR_OUTPUTFORMAT'),
        velociraptor_postprocess        = os.getenv('VELOCIRAPTOR_POSTPROCESS'),
        var_localdevice_concurrency     = int(os.getenv('VAR_LOCALDEVICE_CONCURRENCY')),
        sftp_port			 		    = int(os.getenv('SFTP_PORT')),
        var_sample_size			 	    = int(os.getenv('VAR_SAMPLE_SIZE')),
        var_df_chunksize			    = int(os.getenv('VAR_DF_CHUNKSIZE')),
        blob_storageaccount_enabled     = str_to_bool(os.getenv('BLOB_STORAGEACCOUNT_ENABLED')),
        sftp_enabled                    = str_to_bool(os.getenv('SFTP_ENABLED')),
        keyvault_enabled                = str_to_bool(os.getenv('KEYVAULT_ENABLED')),
        blob_queue_enabled              = str_to_bool(os.getenv('BLOB_QUEUE_ENABLED')),
        blob_logtable_enabled           = str_to_bool(os.getenv('BLOB_LOGTABLE_ENABLED')),
        var_removezip			 	    = str_to_bool(os.getenv('VAR_REMOVEZIP')),
        var_retryfailed			 	    = str_to_bool(os.getenv('VAR_RETRYFAILED')),
        var_verifyuploads			    = str_to_bool(os.getenv('VAR_VERIFYUPLOADS')),
        var_delete_processedzipfiles    = str_to_bool(os.getenv('VAR_DELETE_PROCESSEDZIPFILES')),
        velociraptor_enabled            = str_to_bool(os.getenv('VELOCIRAPTOR_ENABLED')),
        adx_cluster_enabled             = str_to_bool(os.getenv('ADX_CLUSTER_ENABLED')),
        var_add_hostname                = str_to_bool(os.getenv('VAR_ADD_HOSTNAME'))
    )