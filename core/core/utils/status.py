from core.utils.config import load_config
from core.utils.log import generate_sessionid
from core.utils.zip import is_zip_encrypted
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

def _prepare_dictionary_for_upload_to_adx(results_dict, dict_key):
    ''' Prepares dictionary for uploading it to ADX '''

    output = []
    for a in results_dict[dict_key]:
        row = a.copy()
        output.append({
            'uploadid': results_dict['summary'][0]['uploadid'],
            'zipfile': results_dict['summary'][0]['zipfile_basename'],
            **row,
        })

    return output

def upload_detailed_status_to_adx(managers, results, tablename):
    ''' Uploads the detailed status in results dictionary to a table in ADX'''

    if Config.adx_cluster_enabled:

        dict_status = {

            'postprocessing': tablename + '_postprocessing',
            'uploads': tablename + '_uploads',
            'summary': tablename + '_summary'

        }
        
        for key, tablename in dict_status.items():
            results_prepared = _prepare_dictionary_for_upload_to_adx(results, key)
            managers.adx.upload_detailed_status(results_prepared, Config, tablename)

def add_summary_info_to_status(results, zipfile, sessionid, source_name, start):
    ''' Adds summary info to results dictionary '''
    
    results['summary'].append({
        'zipfile_basename': os.path.basename(zipfile),
        'zipfile_fullpath': zipfile,
        'zipfile_size': os.path.getsize(zipfile),
        'sessionid': sessionid,
        'uploadid': 'id' + generate_sessionid(),
        'source_name': source_name,
        'starttime_script': start,
        'is_encrypted': is_zip_encrypted(zipfile)
    })

    return results

def add_hostname_to_status(results, start_postprocessing, hostname):
    ''' Adds hostname to status dictionary '''

    results['summary'][0].update({
        'hostname': hostname,
        'started_postprocessing': start_postprocessing
    })

    return results
