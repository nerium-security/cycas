from sharedlib.utils.config import load_config
import logging as log
import os

log = log.getLogger(__name__)

Config = load_config()

class Status:
    NEW = 'new'
    PROCESSING = 'processing'
    DOWNLOADING = 'downloading'
    DOWNLOADED = 'downloaded'
    DOWNLOADFAILED = 'downloadfailed'
    EXTRACTING = 'extracting'
    EXTRACTED = 'extracted'
    EXTRACTIONFAILED = 'extractionfailed'
    UPLOADING = 'uploading'
    FAILED = 'failed'
    UPLOADFAILED = 'uploadfailed'
    QUEUED = 'queued'
    UNQUEUED = 'unqueued'
    UNKNOWNERROR = 'unknownerror'
    FINISHED = 'finished'
    UPLOADDISABLED = 'uploaddisabled'

def update_status_in_log(managers, processing_status, start, status_data):

    if Config.blob_logtable_enabled:

        managers.table.update_status_in_log(processing_status, start, status_data)

def write_logentry_if_new(managers, processing_status, status_data):

    if Config.blob_logtable_enabled:

        managers.table.writes_log_entry_if_not_exists(processing_status, status_data)

def determine_if_needs_processing(managers, zipfile):

    if not Config.blob_logtable_enabled:
        log.debug('Variable "blob_logtable_enabled" is set to "false". '
                   'Skipping check if it was already processed.')
        return True
    
    zip_basename = os.path.basename(zipfile)
    status_all = managers.table.get_status_zipfile(zip_basename)
    
    if not status_all:
        log.info(f'No status available of {zip_basename} in statustable')
        return False
    
    status = status_all.get('Status')

    if status in [Status.FAILED, Status.NEW, Status.UNQUEUED]:
        log.info(f'Should process. Status is: {status}')
        return True
    else:
        log.debug('Is already processed or processing.')
        return False
    
def upload_detailed_status_to_adx(managers, results, tablename):

    if Config.adx_cluster_enabled:

        managers.adx.upload_detailed_status(tablename, results, Config)