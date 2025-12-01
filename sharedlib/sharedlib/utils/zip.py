import logging as log
import pyzipper
import os
import re
import json
import fnmatch

log = log.getLogger(__name__)

def is_zip_encrypted(zipfile):
    ''' Checks if a zip file is encrypted'''

    with pyzipper.AESZipFile(zipfile) as zf:
        for info in zf.infolist():

            if info.flag_bits & 0x1 or getattr(info, 'is_encrypted', False):
                return True
            else:
                return False

def verify_if_password_works(zipfile, zip_password):
    try:
        with pyzipper.AESZipFile(zipfile) as zf:
            for info in zf.infolist():
                # Check only encrypted entries

                with zf.open(info, pwd=zip_password.encode()) as f:
                    f.read(1)  # Attempt to read just a byte
                    return True  # Successfully decrypted → password works
    except Exception:
        return False

def extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password):
    '''Extract zip file if it is encrypted with a password.'''

    zipfilecontent = list_files_in_zip(zipfile, zip_password)

    for file_in_zip in zipfilecontent:
        
        if 'data.zip' in file_in_zip.filename:

            extracted_zip = extract_single_file(zipfile, file_in_zip, extract_path, zip_password)

            if not extracted_zip:
                return

            zipfilecontent = list_files_in_zip(extracted_zip, zip_password)
            
            return extracted_zip
        
        else:
            return zipfile

def get_extract_path(zip_path, base_extract_dir):
    '''Builds a clean extraction path based on the zip filename.'''
    # Get the filename without .zip
    zip_name = os.path.splitext(os.path.basename(zip_path))[0]

    # Join with base unzip directory
    return os.path.join(base_extract_dir, zip_name)

def extract_single_file(zip_path, file_info, extract_to, password=None):
    '''
    Extracts a single file from a zip archive given an AESZipInfo object.
    Also checks if the extracted file is 'data.zip' and extracts its contents.
    '''
    extracted_path = None
    try:
        with pyzipper.AESZipFile(zip_path, 'r') as zf:
            # Handle encrypted files
            if file_info.flag_bits & 0x1:
                if not password:
                    log.error(f'Password required to extract {file_info.filename}')
                    return None
                zf.pwd = password.encode()

            # Extract the main file
            zf.extract(file_info, path=extract_to)
            extracted_path = os.path.join(extract_to, file_info.filename)
            log.debug(f'Extracted file: {extracted_path}')

    except RuntimeError as e:
        log.error(f'Runtime error while extracting {file_info.filename}: {e}')
    except Exception as e:
        log.error(f'Failed to extract {file_info.filename}: {e}')

    return extracted_path


def is_ignored(file_in_zip, ignorelist):
    '''Check if the filename matches any of the glob patterns in the ignore list.'''

    if file_in_zip.filename.endswith('/'):
        log.debug(f'Ignoring directory: {file_in_zip.filename}')
        return True
    
    if not file_in_zip.filename.endswith(('.jsonl', 'json')):
        log.debug(f'Ignoring non json(l) file: {file_in_zip.filename}')
        return True

    if file_in_zip.file_size == 0:
        log.debug(f'Ignoring empty file: {file_in_zip.filename}')
        return True

    for pattern in ignorelist['ignorelist']:
        filename_only = os.path.basename(file_in_zip.filename)
        if fnmatch.fnmatch(filename_only, pattern):
            log.debug(f'Ignoring file: {file_in_zip}')
            return True

    for pattern in ignorelist['ignorepattern']:
        if fnmatch.fnmatch(file_in_zip.filename, pattern):
            log.debug(f'Ignoring file: {file_in_zip.filename}')
            return True
        
    return False

def load_ignore_list(ignorelist_path):
    '''Load the ignore list from a file.'''
    try:
        with open(ignorelist_path, 'r') as f:
            config = json.load(f)
            log.debug(f'Loaded ignore list from {ignorelist_path}', )
            return config
    except Exception as e:
        log.error(f'Could not load {ignorelist_path}. Error: {str(e)}')
        return []

def list_files_in_zip(zip_path, password=None):
    '''Returns a list of file names contained in the zip archive.'''
    try:
        with pyzipper.AESZipFile(zip_path, 'r') as zf:
            # Set password if encrypted
            encrypted = any(info.flag_bits & 0x1 for info in zf.infolist())
            if encrypted:
                if not password:
                    log.info(f'Password required to list files in encrypted zip: {zip_path}')
                    return []
                zf.pwd = password.encode()

            file_list = zf.infolist()
            log.debug(f'Files in {zip_path}: {file_list}')

            return file_list

    except pyzipper.BadZipFile as e:
        log.error(f'Bad zip file: {e}')
    except RuntimeError as e:
        log.error(f'Runtime error while listing zip: {e}')
    except Exception as e:
        log.error(f'Unexpected error while listing zip: {e}')
    
    return []

def find_computername(filename):
    '''Find computername from name in zipfile.'''
    match = re.search(r'Collection-(.*)-\d{4}-\d{2}-\d{2}', filename)

    if match:
        computer_name = match.group(1)
        log.info(f'Computer name: {computer_name}')
        return computer_name
    else:
        log.warning('Computer name not found.')
        return 'ComputernameNotFound'