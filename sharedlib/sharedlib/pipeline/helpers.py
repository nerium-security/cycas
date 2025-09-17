
from ..utils.config import load_config
from ..utils.files import list_files_in_directory, list_files_in_directory, delete_file, filter_triage_packages
from ..utils.zip import load_ignore_list, list_files_in_zip, extract_single_file, get_extract_path, is_ignored
from ..utils.config import load_config
from ..utils.log import LogManager
from ..utils.auth import Authenticator
from ..utils.status import Status
import json
import os
import logging as log

Config = load_config()

def get_message_in_queue(message_str):
    
    try:

        decoded_message = json.loads(message_str)

        triagepackage = decoded_message.get('triagepackage')
        source_name = decoded_message.get('source_name')

        return source_name, triagepackage
    
    except Exception as e:
        log.error(f'Could not load message from queue: {e}')
        return None, None

def write_logentry_if_new(managers, source_name, zipfile, sessionid, status):

    managers.table.writes_log_entry_if_not_exists(zipfile, source_name, sessionid, status)
    
def should_send_to_queue(managers, zipfile):

    status = managers.table.get_status_zipfile(zipfile)

    if status in [Status.FAILED, Status.NEW, Status.UNQUEUED]:
        log.info(f'Sending to queue. Status is: {status}')
        return True
    else:
        log.info('Is processed or processing.')
    return False

def is_message_not_yet_processing(managers, zipfile) -> bool:
    '''Returns True if the message is currently being processed.'''
    
    status_all = managers.table.get_status_zipfile(zipfile)

    status = status_all.get('Status')

    if status == Status.QUEUED:
        log.info('Message is not yet processed')
        return True
    
    elif status == Status.FAILED:
        log.info('Message was failed.')

    else:
        log.info('Message is already processed or processing')
        return False

def update_status_unqueued(managers, source_name, zipfile, sessionid, start):

    message_not_yet_processed = is_message_not_yet_processing(managers, zipfile)

    if message_not_yet_processed:
        managers.table.update_status_in_log(Status.UNQUEUED, zipfile, source_name, sessionid, start)


def run_zip_processor(managers, source_name, zipfile, sessionid, start):

    should_process = determine_if_needs_processing(managers, zipfile)

    managers.table.update_status_in_log(Status.PROCESSING, zipfile, source_name, sessionid, start)

    if should_process:

        zipfile_downloaded = download_zip(managers, source_name, zipfile, sessionid, start)

        if not zipfile_downloaded:
            return
    else:
        return

    unzip_and_upload(managers, zipfile_downloaded, source_name, sessionid, start)

def unzip_and_upload(managers, zipfile, source_name, sessionid, start):

    log.info(f'Processing {os.path.basename(zipfile)}')

    ignorelist = load_ignore_list(Config.var_location_ignorelist)
    zip_password = managers.keyvault.read_creds(Config.keyvault_passwordlocation)
    zipfilecontent = list_files_in_zip(zipfile, zip_password)
    extract_path = get_extract_path(zipfile, Config.var_unzip_directory)
    extracted_zip = zipfile
    upload_results = {}

    for file_in_zip in zipfilecontent:
        
        if 'data.zip' in file_in_zip.filename:
            extracted_zip = extract_single_file(zipfile, file_in_zip, extract_path, zip_password)
            zipfilecontent = list_files_in_zip(extracted_zip, zip_password)
            delete_file(zipfile)
            continue

    for file_in_zip in zipfilecontent:

        ignored = is_ignored(file_in_zip, ignorelist)

        if ignored:
            continue
        
        extracted_file = extract_single_file(extracted_zip, file_in_zip, extract_path, zip_password)
        if not extracted_file:
            continue

        upload_results[file_in_zip.filename] = upload_file_to_adx(managers, extracted_file, Config.var_test_run)

        deleted = delete_file(extracted_file)

        if not deleted:
            continue
    
    verified = verify_if_all_uploads_are_initiated(upload_results)

    if verified:
        status = Status.FINISHED
    else:
        status = Status.UPLOADFAILED

    if not Config.var_test_run:
        managers.table.update_status_in_log(status, zipfile, source_name, sessionid, start)

def verify_if_all_uploads_are_initiated(results):

    if not results:
        log.warning('No files were uploaded.')
        return False
    
    all_success = True

    for file, success in results.items():
        if not success:
            log.warning(f'Upload failed for file: {file}')
            all_success = False

    if all_success:
        log.info('Initiated upload of all files in zip successfully.')

    return all_success

def upload_file_to_adx(managers, file, test_run):

    if not test_run:

        df = managers.adx.convert_to_dataframe(file, Config.var_sample_size)

        dyn_columns, int_columns = managers.adx.find_dynamic_int_columns(df, Config.var_sample_size)
        managers.adx.convert_dict_to_json(df, dyn_columns)
        tablename = managers.adx.get_tablename(os.path.basename(file))
        cmd_createmergetable = managers.adx.get_table_createcommand(df.columns, tablename, dyn_columns, int_columns)
        managers.adx.launch_createmerge_table(tablename, cmd_createmergetable)
 
        result = managers.adx.launch_upload_file(tablename, file, df)

        if result:
            return True
        else:
            return False
        
    if test_run:
        log.info(f'Test run. Upload of {os.path.basename(file)} not initiated.')

def should_unqueue():
    None

def determine_if_needs_processing(managers, zipfile):
    '''
    should_process = managers.table.determine_if_need_for_processing(zipfile, triagepackage_source, session_id, Status.QUEUED, Config)
    if should_process:
        return True
    else:
        return False
    '''
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

def send_to_queue(managers, source_name, zipfile, sessionid, start):

    managers.queue.send_message(zipfile, source_name)
    managers.table.update_status_in_log(Status.QUEUED, zipfile, source_name, sessionid, start)

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

    if source_name == 'blob':

        all_files = managers.blob.list_blobs(Config.blob_container_input)

    if source_name == 'sftp':

        all_files = managers.sftp.list_files_recursive('/')

    if source_name == 'localfolder':
        
        all_files = list_files_in_directory(Config.var_zip_directory)

    filtered_files = filter_triage_packages(all_files, Config.var_zipfile_prefix, Config.var_zipfile_suffix)

    return filtered_files

def download_zip(managers, source_name, zip, sessionid, start):

    if source_name == 'blob':

            zip = managers.blob.download(Config.blob_container_input, zip, Config.var_zip_directory)

    if source_name == 'sftp':

            zip = managers.sftp.download(Config.var_zip_directory, zip)

    if zip:
        status = Status.DOWNLOADED
    else:
        status = Status.DOWNLOADFAILED

    if not Config.var_test_run:
        managers.table.update_status_in_log(status, zip, source_name, sessionid, start)

    if zip:
        return zip
    else:
        return False

def init(source_name, sessionid):

    os.makedirs(Config.var_loglocation, exist_ok=True)
    os.makedirs(Config.var_zip_directory, exist_ok=True)
    os.makedirs(Config.var_unzip_directory, exist_ok=True)
    logging_manager = LogManager(Config.var_loglocation, Config.var_loglevel)

    log = logging_manager.get_logger(__name__)
    log.info(f'Starting {source_name}2adx pipeline...')
    log.info(f'Session id: {sessionid}')

    auth = Authenticator(Config, source_name)

    #if arg.print_tablestoragelog:
    #    temp = auth.authenticate_azure()
    #    temp = auth.authenticate_tablestorage()
    #    temp.output_logtable()
    #    exit()

    managers = auth.authenticate_all()

    return managers