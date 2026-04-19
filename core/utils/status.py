'''
Status utilities for pipeline processing, logging, and ADX status ingestion.

Defines a `Status` namespace of string constants used throughout the
pipeline, and provides helper functions to:
    - Write and update status entries in Table Storage
    - Determine whether a zipfile should be processed based on stored status
    - Prepare and upload detailed status dictionaries to Azure Data Explorer
    - Enrich results dictionaries with summary metadata such as session ID,
      file size, and hostname

Most functions conditionally execute based on configuration feature flags.
'''

from core.utils.log import generate_sessionid
from core.utils.zip import is_zip_encrypted
import logging as log
import os

log = log.getLogger(__name__)

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

def update_status_in_log(managers, Config, processing_status, starttime, status_data):
    '''
    Update the processing status in the Table Storage log.

    Calls the Table Storage manager to update the status entry if Table
    Storage logging is enabled in configuration.

    Args:
        managers: Container holding authenticated service managers.
        Config: variables defined in .env file
        processing_status (str): New status value to store.
        status_data (dict): Metadata used to build the log entity.
    '''

    if Config.blob_logtable_enabled:

        managers.table.update_status_in_log(processing_status, starttime, status_data)

def write_logentry_if_new(managers, Config, processing_status, status_data):
    '''
    Create a log entry in Table Storage if it does not already exist.

    Writes a new status entry for the given zipfile if Table Storage
    logging is enabled and no existing entry is found.

    Args:
        managers: Container holding authenticated service managers.
        Config: variables defined in .env file
        processing_status (str): Initial status value to store.
        status_data (dict): Metadata used to build the log entity.
    '''

    if Config.blob_logtable_enabled:

        managers.table.writes_log_entry_if_not_exists(processing_status, status_data)

def determine_if_needs_processing(managers, Config, zipfile):
    '''
    Determine whether a zipfile should be processed based on Table Storage status.

    If Table Storage logging is disabled, processing is always allowed.
    Otherwise, the zipfile basename is looked up in the status table and
    processing is allowed only if the stored status indicates a new or
    failed item.

    Args:
        managers: Container holding authenticated service managers.
        zipfile (str): Full path to the zipfile being evaluated.

    Returns:
        bool: True if the zipfile should be processed, otherwise False.
    '''

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
    '''
    Prepare a detailed results sub-dictionary for ingestion into ADX.

    Flattens the list stored under `results_dict[dict_key]` and enriches each
    row with identifiers from the summary entry (uploadid and zipfile basename).

    Args:
        results_dict (dict): Results structure containing a 'summary' list and
            one or more detailed lists (e.g. 'uploads', 'postprocessing').
        dict_key (str): Key identifying the list to flatten and enrich.

    Returns:
        list[dict]: List of row dictionaries suitable for ingestion into ADX.
    '''

    output = []
    for a in results_dict[dict_key]:
        row = a.copy()
        output.append({
            'uploadid': results_dict['summary'][0]['uploadid'],
            'zipfile': results_dict['summary'][0]['zipfile_basename'],
            **row,
        })

    return output

def upload_detailed_status_to_adx(managers, Config, results, tablename):
    '''
    Upload detailed pipeline status results to Azure Data Explorer (ADX).

    When ADX ingestion is enabled, uploads the 'postprocessing', 'uploads',
    and 'summary' parts of the results dictionary to separate ADX tables
    derived from the provided base table name.

    Args:
        managers: Container holding authenticated service managers.
        results (dict): Results dictionary containing keys 'postprocessing',
            'uploads', and 'summary'.
        tablename (str): Base table name used to generate table names for
            detailed status ingestion.
    '''

    if Config.adx_cluster_enabled:

        dict_status = {

            'uploads': tablename + '_uploads',
            'summary': tablename + '_summary'

        }

        if results.get('postprocessing'):

            dict_status['postprocessing'] = tablename + '_postprocessing'

        for key, tablename in dict_status.items():
            results_prepared = _prepare_dictionary_for_upload_to_adx(results, key)
            managers.adx.upload_detailed_status(results_prepared, Config, tablename)

def add_summary_info_to_status(results, zipfile, sessionid, source_name, start):
    '''
    Add summary metadata for a zipfile run into the results dictionary.

    Appends a single summary record containing zipfile metadata and run
    context, including file size, session identifier, a generated upload ID,
    and whether the zipfile is encrypted.

    Args:
        results (dict): Results dictionary expected to contain a 'summary' list.
        zipfile (str): Full path to the zipfile being processed.
        sessionid (str): Session identifier for the current run.
        source_name (str): Source identifier associated with the zipfile.
        start (datetime): Start timestamp for the script/run.

    Returns:
        dict: Updated results dictionary.
    '''

    results['summary'].append({
        'zipfile_basename': os.path.basename(zipfile),
        'zipfile_fullpath': zipfile,
        'zipfile_size': os.path.getsize(zipfile) if os.path.exists(zipfile) else 0,
        'sessionid': sessionid,
        'uploadid': 'id' + generate_sessionid(),
        'source_name': source_name,
        'starttime_script': start,
        'is_encrypted': is_zip_encrypted(zipfile)
    })

    return results

def add_hostname_to_status(results, start_postprocessing, hostname):
    '''
    Add hostname and post-processing start time to the summary record.

    Updates `results['summary'][0]` with the extracted hostname and the
    timestamp indicating when post-processing started.

    Args:
        results (dict): Results dictionary containing a 'summary' list with
            at least one element.
        start_postprocessing (datetime): Timestamp when post-processing began.
        hostname (str): Hostname extracted from the triage package.

    Returns:
        dict: Updated results dictionary.
    '''

    results['summary'][0].update({
        'hostname': hostname,
        'started_postprocessing': start_postprocessing
    })

    return results
