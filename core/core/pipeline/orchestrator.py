'''
Main pipeline orchestration functions for processing triage zip packages.

This module coordinates end-to-end processing of triage packages from various
sources (blob, sas, sftp, localfolder) into Azure Data Explorer (ADX). It:
    - Downloads zip files when required
    - Handles encrypted zip extraction (including nested data.zip)
    - Optionally post-processes raw artifacts using Velociraptor
    - Extracts JSON/JSONL files and initiates ADX ingestion
    - Updates status transitions in Table Storage
    - Uploads detailed run status to ADX
'''

from core.utils.config import load_config
from core.utils.files import split_jsonl_by_size, delete_file, get_filesize_bytes
from core.utils.zip import zip_contains_raw_artifacts, get_password_from_env_or_prompt, load_ignore_list, list_files_in_zip, extract_single_file, get_extract_path, is_ignored, extract_encrypted_and_non_encrypted_zipfiles, get_hostname_from_filename
from core.utils.postprocess import download_velociraptor, build_remap, find_hostname, load_artifacts, select_artifacts, postprocess
from core.utils.summary import define_results_upload_dict, define_results_dict, get_duration_from_timespan, pretty_print_summary_per_zip, summary_per_zip_to_file
from core.utils.misc import should_download, send_webhook
from core.utils.auth import Authenticator
from core.utils.status import Status, update_status_in_log, write_logentry_if_new, determine_if_needs_processing, upload_detailed_status_to_adx, add_summary_info_to_status, add_hostname_to_status
from datetime import datetime
import os
import logging as log

log = log.getLogger(__name__)

Config = load_config()

def run_zip_processor(managers, source_name, zipfile, sessionid, message):
    '''
    Process a single zipfile end-to-end.

    Args:
        managers: Container holding authenticated service managers.
        source_name (str): Data source identifier (e.g. 'blob', 'sas', 'sftp', 'localfolder').
        zipfile (str): Zipfile path or remote identifier depending on source.
        sessionid (str): Session identifier for the current run.
        message: Queue message object associated with this zipfile (if applicable).
    '''

    # ------------------------------------------------------------------
    # Initialize run context
    # ------------------------------------------------------------------
    start = datetime.now()

    results = define_results_dict()

    results = add_summary_info_to_status(results, zipfile, sessionid, source_name, start)
    
    write_logentry_if_new(managers, Config, Status.NEW, results)


    # ------------------------------------------------------------------
    # Determining if required to continue
    # ------------------------------------------------------------------
    should_process = determine_if_needs_processing(managers, Config, zipfile)

    if not should_process:
        log.info(f'Is already processing {zipfile}')
        return

    update_status_in_log(managers, Config, Status.PROCESSING, start, results)


    # ----------------------------------------------------------------------
    # Download zip file
    # ----------------------------------------------------------------------
    if should_download(source_name):

        zipfile = _download_zip(managers, source_name, zipfile, sessionid, start, results)


    # ----------------------------------------------------------------------
    # Extract zipfile if encrypted
    # ----------------------------------------------------------------------
    log.info(f'Processing {os.path.basename(zipfile)}')

    if Config.keyvault_enabled:
        zip_password = managers.keyvault.read_creds(Config.keyvault_passwordlocation)
    else:
        zip_password = ''

    extract_path = get_extract_path(zipfile, Config.var_unzip_directory)

    extracted_zip, unextracted_zip = extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password)

    if unextracted_zip:

        zip_password, extracted_zip = get_password_from_env_or_prompt(source_name, unextracted_zip)

        if not zip_password:
            log.info('Password false. Skipping zip.')
            return

    zipfilecontent = list_files_in_zip(extracted_zip, zip_password)


    # ----------------------------------------------------------------------
    # Post-process raw artifacts and upload it's results (json)
    # ----------------------------------------------------------------------
    results = postprocess_velociraptor_and_upload(managers,
                                                  extracted_zip, 
                                                  zipfilecontent,
                                                  results)

    # ----------------------------------------------------------------------
    # Extract jsons from zip and upload
    # ----------------------------------------------------------------------
    results = extract_all_json_from_zip_and_upload(managers, 
                                                   extracted_zip, 
                                                   extract_path, 
                                                   zip_password, 
                                                   zipfilecontent, 
                                                   results)


    # ----------------------------------------------------------------------
    # Wrapping up by updating logstatus
    # ----------------------------------------------------------------------
    results.update({'finished': True})

    update_status_in_log(managers, Config, Status.FINISHED, start, results)

    upload_detailed_status_to_adx(managers, Config, results, tablename='_status')

    _send_webhook_message(Config.var_webhook_url, results)

def postprocess_velociraptor_and_upload(managers, zipfile, zipfilecontent, results):
    '''
    Post-process raw artifacts in a triage zip using Velociraptor and upload outputs to ADX.

    When Velociraptor post-processing is enabled and the zip contents indicate
    the presence of raw artifacts (typically under an 'uploads/' folder), this
    function:
        - Ensures the Velociraptor binary is available (downloads if needed)
        - Generates a remapping file for reading the zip contents
        - Extracts a hostname from the SYSTEM registry hive via the remap
        - Loads and selects configured artifact queries ('essential' or 'full')
        - Executes each artifact query and writes an output file in the configured format
        - Initiates ADX ingestion for each generated output file
        - Deletes generated output files after ingestion is initiated
        - Appends post-processing and upload metadata to the results dictionary

    Args:
        managers: Container holding authenticated service managers. Requires an ADX
            manager when ADX ingestion is enabled.
        zipfile (str): Path to the zip archive being post-processed.
        zipfilecontent (list): List of ZipInfo-like entries from the zip archive.
            Used to determine whether raw artifacts are present.
        results (dict): Results structure to update. Expected to contain 'summary',
            'postprocessing', and 'uploads' keys.

    Returns:
        dict: Updated results dictionary. If Velociraptor is disabled or the zip
        does not contain raw artifacts, returns `results` unchanged.
    '''

    log.info(f'Post-processing is set to: {Config.velociraptor_enabled}')
    if not Config.velociraptor_enabled:
        return results


    # ----------------------------------------------------------------------
    # Loading config for Velociraptor
    # ----------------------------------------------------------------------
    remappingdir = Config.velociraptor_remappingdir
    binary = Config.velociraptor_binary
    definitions = Config.velociraptor_definitions
    url = Config.velociraptor_url
    binary = Config.velociraptor_binary
    unzip_dir = Config.var_unzip_directory
    outputformat = Config.velociraptor_outputformat
    artifactslist = Config.velociraptor_artifactslist
    postprocess_var = Config.velociraptor_postprocess

    if not zip_contains_raw_artifacts(zipfilecontent):
        return results


    # ----------------------------------------------------------------------
    # Downloading Velociraptor, build remap, and find hostname in SYSTEM
    # ----------------------------------------------------------------------
    download_velociraptor(binary, url)

    remappingfile = build_remap(zipfile, 
                                remappingdir,
                                binary,
                                definitions,
                                unzip_dir)
    
    hostname = find_hostname(remappingfile,
                                binary,
                                definitions)
    

    # ----------------------------------------------------------------------
    # Loading Velociraptor artifacts
    # ----------------------------------------------------------------------    
    artifacts_json = load_artifacts(artifactslist)
    artifacts = select_artifacts(artifacts_json, postprocess_var)

    start_postprocessing = datetime.now()

    add_hostname_to_status(results, start_postprocessing, hostname)


    # ----------------------------------------------------------------------
    # Post-processing raw-artifacts with Velociraptor
    # ----------------------------------------------------------------------   
    for artifact in artifacts:

        result_postprocess = postprocess(hostname, 
                                            artifact, 
                                            zipfile, 
                                            definitions, 
                                            unzip_dir, 
                                            binary, 
                                            outputformat,
                                            remappingfile)

        results['postprocessing'].append(result_postprocess)
        outputfile_path = result_postprocess.get('fullpath')
        result_postprocess.pop('fullpath', None)
        result_upload = define_results_upload_dict()
        
        result_upload.update(_upload_file_to_adx(managers, outputfile_path))

        delete_file(outputfile_path)
        
        result_upload['was_postprocessed_with'] = artifact
        results['uploads'].append(result_upload)
        
    duration = get_duration_from_timespan(start_postprocessing)

    if Config.adx_cluster_enabled:

        log.info(f'Processing and uploading all post-processed artifacts took {duration}..')

    else:
        log.info(f'Post-processing all artifacts took {duration}..')
    
    return results

def extract_all_json_from_zip_and_upload(managers, 
                                         extracted_zip, 
                                         extract_path, 
                                         zip_password, 
                                         zipfilecontent, 
                                         results):
    '''
    Extract JSON and JSONL files from a zip archive and initiate ADX ingestion.

    Args:
        managers: Container holding authenticated service managers. Requires an
            ADX manager when ADX ingestion is enabled.
        extracted_zip (str): Path to the zip archive being processed (may be the
            original zip or an extracted nested 'data.zip').
        extract_path (str): Local directory where files are extracted.
        zip_password (str | None): Password for encrypted archives, if required.
        zipfilecontent (list): List of ZipInfo-like entries returned by
            `list_files_in_zip()`.
        results (dict): Results structure to update. Must include an 'uploads'
            list and a 'summary' list with at least one element when ADX is enabled.

    Returns:
        dict: Updated results dictionary, with per-file upload metadata appended
        to `results['uploads']`.
    '''

    # ----------------------------------------------------------------------
    # Initilializing the extraction
    # ----------------------------------------------------------------------  
    ignorelist = load_ignore_list(Config.var_location_ignorelist)
    
    if Config.adx_cluster_enabled:
        hostname = results['summary'][0].get('hostname')

    # ----------------------------------------------------------------------
    # Extract file by file, add hostname to file, and upload jsons
    # ----------------------------------------------------------------------  
    for file_in_zip in zipfilecontent:

        upload_dict = define_results_upload_dict()
        filename = file_in_zip.filename
        upload_dict['location_in_zip'] = filename

        upload_dict.update(is_ignored(file_in_zip, ignorelist))

        if upload_dict.get('ignored_upload'):
            results['uploads'].append(upload_dict)
            continue
        
        extracted_file = extract_single_file(extracted_zip, file_in_zip, extract_path, zip_password)

        if not extracted_file:
            continue

        if hostname:
            upload_dict.update(managers.adx.add_hostname_to_file(extracted_file, hostname, extracted_zip))

            MAX_ADX_UPLOAD_SIZE = 6_442_450_944  # 6 GB

            if get_filesize_bytes(extracted_file) >= MAX_ADX_UPLOAD_SIZE:
                file_is_split = True
                files_to_upload = split_jsonl_by_size(extracted_file)
            else:
                file_is_split = False
                files_to_upload = [extracted_file]

            for file_path in files_to_upload:

                upload_dict.update(_upload_file_to_adx(managers, file_path))

                results['uploads'].append(upload_dict)
                delete_file(file_path)
            
            if file_is_split:
                delete_file(extracted_file)

    return results

def _upload_file_to_adx(managers, file):
    '''
    Create or update an ADX table and initiate upload of a data file.

    Ensures the target table exists (creating or merging schema if needed)
    and initiates ingestion of the specified file into Azure Data Explorer.

    Args:
        managers: Container holding authenticated service managers.
        file (str): Path to the local file to be uploaded.

    Returns:
        dict: Upload result metadata returned by the ADX ingestion client.
    '''

    if Config.adx_cluster_enabled:

        tablename = managers.adx.create_new_table_if_required(Config, file, forcetablename=False)

        upload_result = managers.adx.launch_upload_file(tablename, file)

    return upload_result

def _download_zip(managers, source_name, zip, sessionid, start, status_data):
    '''
    Download a ZIP file from the configured data source and update its status.

    Initiates a download from the specified source (blob storage, SFTP, or
    SAS container), updates the processing status before and after the
    operation, and returns the local file path on success.

    Args:
        managers: Container holding authenticated service managers.
        source_name (str): Data source type.
            Supported values: 'blob', 'sftp', 'sas'.
        zip (str): Source-specific ZIP identifier or path.
        sessionid (str): Session identifier for the current pipeline run.
        start (datetime): Timestamp marking the start of the pipeline step.
        status_data (dict): Status payload used for log table updates.

    Returns:
        str | bool: Local filesystem path to the downloaded ZIP file if
        successful, otherwise False.
    '''

    update_status_in_log(managers, Config, Status.DOWNLOADING, start, status_data)

    if source_name == 'blob':

        zip = managers.blob.download(Config.blob_container_input, zip, Config.var_download_directory)

    if source_name == 'sftp':

        zip = managers.sftp.download(Config.var_download_directory, zip)

    if source_name == 'sas':

        zip = managers.sas.download(Config.var_download_directory, zip)

    if zip:
        status = Status.DOWNLOADED
    else:
        status = Status.DOWNLOADFAILED

    update_status_in_log(managers, Config, status, start, status_data)

    if zip:
        return zip
    else:
        return False

def _send_webhook_message(webhook_url, results):
    '''
    Send a completion message to a webhook endpoint.

    Builds a human-readable summary message based on processing results
    and sends it to the configured webhook URL.
    '''

    if not webhook_url:
        return

    # Retrieve count of successful uploads
    nr_uploads = 0
    for f in results['uploads']:
        if not isinstance(f, dict):
            continue

        if not f.get('upload_initiated'):
            continue

        if f.get('upload_error') is not None:
            continue

        if not isinstance(f.get('basename'), str):
            continue

        nr_uploads += 1

    # Retrieve count of postprocessed artefacts
    nr_postprocessed = 0
    for f in results['postprocessing']:
        if not isinstance(f, dict):
            continue

        if not f.get('success'):
            continue

        if f.get('error') is not None:
            continue

        if not isinstance(f.get('basename'), str):
            continue

        nr_postprocessed += 1

    if webhook_url:
        basename = results['summary'][0].get('zipfile_basename')
        message = (
            f'{basename} finished.'
            f'Postprocessed {nr_postprocessed} artifacts. '
            f'Upload in total {nr_uploads} json files.'
        )
        
        send_webhook(webhook_url, message)

def init(source_name, sessionid):
    '''
    Initialize the pipeline runtime and authenticate all required services.

    Ensures required local directories exist, initializes logging context,
    and authenticates all service managers needed for the selected data source.

    Args:
        source_name (str): Name of the data source being processed
            (e.g. 'blob', 'sas', 'sftp', 'localfolder').
        sessionid (str): Unique identifier for the current pipeline run.

    Returns:
        AuthManagers: Container holding authenticated service managers
        for the enabled components.
    '''

    try:
        os.makedirs(Config.var_loglocation, exist_ok=True)
        os.makedirs(Config.var_localfolder_directory, exist_ok=True)
        os.makedirs(Config.var_unzip_directory, exist_ok=True)
    except Exception as e:
        log.error(f'Could not create directory: {e}')

    log.info(f'Starting {source_name}2adx pipeline...')
    log.info(f'Session id: {sessionid}')

    auth = Authenticator(Config, source_name)

    managers = auth.authenticate_all()

    return managers