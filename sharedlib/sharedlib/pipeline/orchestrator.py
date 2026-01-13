from sharedlib.utils.config import load_config
from sharedlib.utils.files import split_jsonl_by_size, list_files_in_directory, list_files_in_directory, delete_file, filter_triage_packages, get_filesize_bytes
from sharedlib.utils.zip import zip_contains_raw_artifacts, get_password_from_env_or_prompt, load_ignore_list, list_files_in_zip, extract_single_file, get_extract_path, is_ignored, extract_encrypted_and_non_encrypted_zipfiles, get_hostname_from_filename
from sharedlib.utils.postprocess import download_velociraptor, build_remap, find_hostname, load_artifacts, select_artifacts, postprocess
from sharedlib.utils.summary import define_results_upload_dict, define_results_dict, get_duration_from_timespan, pretty_print_summary_per_zip, summary_per_zip_to_file
from sharedlib.utils.misc import send_webhook
from sharedlib.utils.auth import Authenticator
from sharedlib.utils.status import Status, update_status_in_log, write_logentry_if_new, determine_if_needs_processing, upload_detailed_status_to_adx, add_summary_info_to_status, add_hostname_to_status

from datetime import datetime
import os
import logging as log

log = log.getLogger(__name__)

Config = load_config()

def run_zip_processor(managers, source_name, zipfile, sessionid, message):

    start = datetime.now()

    results = define_results_dict()

    results = add_summary_info_to_status(results, zipfile, sessionid, source_name, start)
    
    write_logentry_if_new(managers, Status.NEW, results)

    should_process = determine_if_needs_processing(managers, zipfile)

    if not should_process:
        log.info(f'Is already processing {zipfile}')
        return

    update_status_in_log(managers, Status.PROCESSING, start, results)
   
    if should_download(source_name):

        zipfile = download_zip(managers, source_name, zipfile, sessionid, start, results)

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

    results = postprocess_velociraptor_and_upload(managers,
                                                  extracted_zip, 
                                                  zipfilecontent,
                                                  results)
    
    results = extract_all_json_from_zip_and_upload(managers, 
                                                   extracted_zip, 
                                                   extract_path, 
                                                   zip_password, 
                                                   zipfilecontent, 
                                                   results)
    
    results.update({'finished': True})

    update_status_in_log(managers, Status.FINISHED, start, results)

    upload_detailed_status_to_adx(managers, results, tablename='_status')

def should_download(source_name):
    if source_name == 'localfolder':
        return False
    else:
        return True

def postprocess_velociraptor_and_upload(managers, zipfile, zipfilecontent, results):
    ''' Post-processing with Velociraptor and upload json file output to adx.'''

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
            return results

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

        add_hostname_to_status(results, start_postprocessing, hostname)

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
            
            result_upload.update(upload_file_to_adx(managers, outputfile_path))

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
    '''Extract and upload json files to ADX'''

    ignorelist = load_ignore_list(Config.var_location_ignorelist)
    
    if Config.adx_cluster_enabled:
        hostname = results['summary'][0].get('hostname')

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

                upload_dict.update(upload_file_to_adx(managers, file_path))

                results['uploads'].append(upload_dict)
                delete_file(file_path)
            
            if file_is_split:
                delete_file(extracted_file)

    return results

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

    update_status_in_log(managers, status, start, results)

    if Config.var_webhook_url:
        message = f'{status} of {zipfile}'
        send_webhook(Config.var_webhook_url, message)

    return all_success

def upload_file_to_adx(managers, file):
    '''Prepares the adx table and uploads the file'''

    if Config.adx_cluster_enabled:

        tablename = managers.adx.create_new_table_if_required(Config, file, forcetablename=False)

        upload_result = managers.adx.launch_upload_file(tablename, file)

    return upload_result

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

def download_zip(managers, source_name, zip, sessionid, start, status_data):

    update_status_in_log(managers, Status.DOWNLOADING, start, status_data)

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

    update_status_in_log(managers, status, start, status_data)

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