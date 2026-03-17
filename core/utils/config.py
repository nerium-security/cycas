'''
Module for loading application configuration from environment variables.

Defines a `Config` dataclass representing all supported settings and
provides helpers to load values from a `.env` file using python-dotenv.

Configuration values are loaded from the process environment, with
selected fields converted to booleans and integers.
'''

from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import find_dotenv
import logging as log

log = log.getLogger(__name__)

class Config(BaseSettings):
    '''
    It reads variables from the environment. If it cannot find it, it
    loads from a .env using python-dotenv

    Returns:
        Config: Configuration populated from environment variables.

    Raises:
        ValueError: If integer conversions fail (e.g. VAR_SAMPLE_SIZE is not an int).
        TypeError: If required environment variables are missing and `int(None)` occurs.
    '''

    model_config = SettingsConfigDict(
        env_file=find_dotenv(),
        env_file_encoding='utf-8'
    )

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
    var_removezip: bool
    var_zipfile_prefix: str
    var_zipfile_suffix: str
    var_location_ignorelist: str
    var_retryfailed: bool
    var_verifyuploads: bool
    var_loglevel: str
    var_delete_processedzipfiles: bool
    var_localdevice_concurrency: int
    var_add_hostname: bool
    var_artifact_summary_filename: str
    var_master_summary_filename: str
    velociraptor_enabled: bool
    velociraptor_url: str
    velociraptor_remappingdir: str
    velociraptor_binary: str
    velociraptor_definitions: str
    velociraptor_outputformat: str
    velociraptor_artifactslist: str
    velociraptor_postprocess: str
    velociraptor_duration: int

def load_config() -> Config:
    '''Return the configuration.'''
    return Config()
