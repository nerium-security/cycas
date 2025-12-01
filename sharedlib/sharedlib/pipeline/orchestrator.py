from sharedlib.utils.config import load_config
from sharedlib.utils.files import split_jsonl_by_size, list_files_in_directory, list_files_in_directory, delete_file, filter_triage_packages, get_filesize_bytes
from sharedlib.utils.zip import get_password_from_env_or_prompt, load_ignore_list, list_files_in_zip, extract_single_file, get_extract_path, is_ignored, extract_encrypted_and_non_encrypted_zipfiles, get_hostname_from_filename
from sharedlib.utils.postprocess import download_velociraptor, build_remap, find_hostname, load_artifacts, select_artifacts, postprocess
from sharedlib.utils.log import get_duration_from_timespan
from sharedlib.utils.misc import send_webhook
from sharedlib.utils.auth import Authenticator
from sharedlib.utils.status import Status, update_status_in_log, write_logentry_if_new, determine_if_needs_processing
from datetime import datetime
import os
import logging as log

log = log.getLogger(__name__)

Config = load_config()

def run_zip_processor(managers, source_name, zipfile, sessionid, message):

    start = datetime.now()

    write_logentry_if_new(managers, source_name, zipfile, sessionid, Status.NEW)

    should_process = determine_if_needs_processing(managers, zipfile)

    if not should_process:
        return

    update_status_in_log(managers, Status.PROCESSING, zipfile, source_name, sessionid, start)

    if should_download(source_name):

        zipfile = download_zip(managers, source_name, zipfile, sessionid, start)

    log.info(f'Processing {os.path.basename(zipfile)}')

    zip_password = get_zip_password_from_keyvault(managers)

    extract_path = get_extract_path(zipfile, Config.var_unzip_directory)

    extracted_zip, unextracted_zip = extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password)

    if unextracted_zip:

        zip_password, extracted_zip = get_password_from_env_or_prompt(source_name, unextracted_zip)

        if not zip_password:
            log.info('Password false. Skipping zip.')
            return

    zipfilecontent = list_files_in_zip(extracted_zip, zip_password)

    upload_results, hostname = postprocess_velociraptor_and_upload(managers,
                                                         extracted_zip, 
                                                         zipfilecontent)

    upload_results = extract_all_json_from_zip_and_upload(managers, 
                                                          extracted_zip, 
                                                          extract_path, 
                                                          zip_password, 
                                                          zipfilecontent, 
                                                          upload_results,
                                                          hostname)

    verify_if_all_uploads_are_initiated(managers, 
                                        upload_results, 
                                        zipfile, 
                                        source_name, 
                                        sessionid, 
                                        start,
                                        message)

def should_download(source_name):
    if source_name == 'localfolder':
        return False
    else:
        return True

def zip_contains_raw_artifacts(content):
    
    for file_in_zip in content:

        if file_in_zip.filename.startswith('uploads/'):
            log.info('Starts post-processing as zipfile seems to contain raw artifacts in \'uploads\' folder')
            
            return True
    
    log.info('Skipping post-processing as zipfile does not '
             'seem to contain raw artifacts typically stored in \'uploads\' folder')
    return False

def postprocess_velociraptor_and_upload(managers, zipfile, zipfilecontent):
    ''' Post-processing with Velociraptor and upload json file output to adx.'''

    upload_results = {}
    
    log.info(f'Post-processing is set to: {Config.velociraptor_enabled}')
    if Config.velociraptor_enabled:

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
            return upload_results, ''

        download_velociraptor(binary, url)

        remappingfile = build_remap(zipfile, 
                                    remappingdir,
                                    binary,
                                    definitions,
                                    unzip_dir)
        
        hostname = find_hostname(remappingfile,
                                 binary,
                                 definitions)
        
        artifacts_json = load_artifacts(artifactslist)
        artifacts = select_artifacts(artifacts_json, postprocess_var)

        start_postprocessing = datetime.now()
        
        for artifact in artifacts:

            postprocessed_json = postprocess(hostname, 
                                             artifact, 
                                             zipfile, 
                                             definitions, 
                                             unzip_dir, 
                                             binary, 
                                             outputformat,
                                             remappingfile)

            if postprocessed_json:

                upload_results[postprocessed_json] = upload_file_to_adx(managers, postprocessed_json)
                delete_file(postprocessed_json)

        duration = get_duration_from_timespan(start_postprocessing)

        if Config.adx_cluster_enabled:

            log.info(f'Processing and uploading all post-processed artifacts took {duration}..')

        else:
            log.info(f'Post-processing all artifacts took {duration}..')

    return upload_results, hostname


def extract_all_json_from_zip_and_upload(managers, 
                                         extracted_zip, 
                                         extract_path, 
                                         zip_password, 
                                         zipfilecontent, 
                                         upload_results,
                                         hostname):
    '''Extract and upload json files to ADX'''

    ignorelist = load_ignore_list(Config.var_location_ignorelist)
    
    if Config.adx_cluster_enabled:
        if not hostname:
            hostname = get_hostname_from_filename(extracted_zip)

    for file_in_zip in zipfilecontent:
        
        ignored = is_ignored(file_in_zip, ignorelist)
        
        if ignored:
            continue

        extracted_file = extract_single_file(extracted_zip, file_in_zip, extract_path, zip_password)

        if not extracted_file:
            continue
        
        if Config.var_add_hostname:

            if Config.adx_cluster_enabled:
                managers.adx.add_hostname_to_file(extracted_file, hostname, extracted_zip)

        if get_filesize_bytes(extracted_file) >= 6442450944:

            extracted_file_list = split_jsonl_by_size(extracted_file)

            delete_file(extracted_file)

        else:

            extracted_file_list = []
            extracted_file_list.append(extracted_file)

        for extracted_file in extracted_file_list:

            upload_results[file_in_zip.filename] = upload_file_to_adx(managers, extracted_file)

            delete_file(extracted_file)

    return upload_results

def get_zip_password_from_keyvault(managers):

    if Config.keyvault_enabled:
        zip_password = managers.keyvault.read_creds(Config.keyvault_passwordlocation)
    else:
        zip_password = ''

    return zip_password


def verify_if_all_uploads_are_initiated(managers, results, zipfile, source_name, sessionid, start, message):
    
    duration = get_duration_from_timespan(start)
    
    log.info(f'ZIP processing finished in: {duration}')
    if Config.blob_queue_enabled:
        managers.queue.delete_message(message)

    all_success = []

    if Config.adx_cluster_enabled:

        if not results:
            log.warning('No files were uploaded.')
            return False
        
        all_success = True

        for file, success in results.items():
            if not success:
                log.warning(f'Upload failed for file: {file}')
                all_success = False

        if all_success:
            log.info('Initiated upload of files in zip successfully.')

        if all_success:
            status = Status.FINISHED
        else:
            status = Status.UPLOADFAILED

    if not Config.adx_cluster_enabled:
        status = Status.UPLOADDISABLED

    update_status_in_log(managers, status, zipfile, source_name, sessionid, start)

    if Config.var_webhook_url:
        message = f'{status} of {zipfile}'
        send_webhook(Config.var_webhook_url, message)

    return all_success

def upload_file_to_adx(managers, file):
    '''Prepares the adx table and uploads the file'''

    if Config.adx_cluster_enabled:

        tablename = managers.adx.create_new_table_if_required(Config, file)

        result = managers.adx.launch_upload_file(tablename, file)

        if result:
            return True
        else:
            return False
        
    else:
        return False

def remove_zip(managers, source_name, zipfile_downloaded, zip):

    delete_file(zipfile_downloaded)

    if Config.var_delete_processedzipfiles:
    
        if source_name == 'blob':

            managers.blob.delete(Config.blob_container_input, zip)

        if source_name == 'sftp':

            managers.sftp.delete(zip)

        return True

    else:
        return False

def list_zipfiles(managers, source_name):

    log.info(f'Attempting to find zip files in datasource: {source_name}')

    try:

        if source_name == 'blob':

            all_files = managers.blob.list_blobs(Config.blob_container_input)

        if source_name == 'sas':

            all_files = managers.sas.list_blobs_from_sas()

        if source_name == 'sftp':

            all_files = managers.sftp.list_files_recursive('/')

        if source_name == 'localfolder':

            all_files = list_files_in_directory(Config.var_localfolder_directory)

    except:
        log.error(f'Could not list files in: {source_name}. Was this source enabled in .env file?')
        return []

    filtered_files = filter_triage_packages(all_files, Config.var_zipfile_prefix, Config.var_zipfile_suffix)

    return filtered_files

def download_zip(managers, source_name, zip, sessionid, start):

    update_status_in_log(managers, Status.DOWNLOADING, zip, source_name, sessionid, start)

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

    update_status_in_log(managers, status, zip, source_name, sessionid, start)

    if zip:
        return zip
    else:
        return False

def init(source_name, sessionid):

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