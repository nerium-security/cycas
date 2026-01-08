import logging as log
import pyzipper
import os
import re
import json
import fnmatch
import getpass
import glob

log = log.getLogger(__name__)

def is_zip_encrypted(zipfile):
    ''' Checks if a zip file is encrypted'''

    with pyzipper.AESZipFile(zipfile) as zf:
        for info in zf.infolist():

            if info.flag_bits & 0x1 or getattr(info, 'is_encrypted', False):
                return True
            else:
                return False

def get_hostname_from_filename(fullpath):
    ''' 
    Extracts the hostname from filename using regex. 
    
    Examples of hostnames that are extracted:
    
    - Collection-HOSTNAME-2024-03-01T16_10_46Z.zip
    - HOSTNAME-2025-03-01T16_10_46Z.zip
    - LAPTOP-DC-C.65e548a6aa01faa1-F.D3DN1LABPD0OA
    '''

    if fullpath:
        if fullpath.upper().startswith("H."):
            return ''
        elif fullpath.upper().startswith("Hunt H."):
            return ''

    pattern = re.compile(
        r'(?:collection-)?'                                                     # optional prefix
        r'([A-Z0-9-]+?)'                                                        # hostname
        r'(?=_|-[A-Z]\.[0-9a-f]{6,}|-C\.|-202[0-9]-[0-9]{2}-[0-9]{2}T|\.|$)',   # stop here (lookahead)
        re.IGNORECASE
    )

    if fullpath:
        match = pattern.search(fullpath)
        if match:
            match = match.group(1)
            log.info(f'Extracted hostname from zipfilename: {match}')
            return match
        else:
            return ''

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

def zip_contains_raw_artifacts(content):
    
    for file_in_zip in content:

        if file_in_zip.filename.startswith('uploads/'):
            log.info('Starts post-processing as zipfile seems to contain raw artifacts in \'uploads\' folder')
            
            return True
    
    log.info('Skipping post-processing as zipfile does not '
             'seem to contain raw artifacts typically stored in \'uploads\' folder')
    return False

def extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password):
    '''Extract zip file if it is encrypted with a password.'''

    zipfilecontent = list_files_in_zip(zipfile, zip_password)
    
    if not zipfilecontent:
        return None, zipfile

    for file_in_zip in zipfilecontent:
        
        if 'data.zip' in file_in_zip.filename:

            extracted_zip = extract_single_file(zipfile, file_in_zip, extract_path, zip_password)
            
            if not extracted_zip:
                
                return None, zipfile
            
            zipfilecontent = list_files_in_zip(extracted_zip, zip_password)
            
            return extracted_zip, None
        
        else:
            return zipfile, None

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
    '''Determines wheter a file should be ignored for further processing or not'''

    filename = file_in_zip.filename

    def result(ignored, reason=None, pattern=None):
        if ignored:
            log.debug(f'Ignoring: {file_in_zip.filename} | reason={reason} | pattern={pattern}')
        return {
            'filename': filename,
            'ignored': ignored,
            'reason': reason,
            'pattern': pattern,
        }

    if filename.endswith('/'):
        return result(True, reason='is directory')

    if not filename.endswith(('.jsonl', '.json')):
        return result(True, reason='not json/jsonl')

    if file_in_zip.file_size == 0:
        return result(True, reason='empty file')

    filename_only = os.path.basename(filename)

    for pattern in ignorelist.get('ignorelist', []):
        if fnmatch.fnmatch(filename_only, pattern):
            return result(True, reason='ignored by basename pattern in ignorelist', pattern=pattern)

    for pattern in ignorelist.get('ignorepattern', []):
        if fnmatch.fnmatch(filename, pattern):
            return result(True, reason='ignored by path pattern in ignorelist', pattern=pattern)

    return result(False)

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
                    log.info(f'Cannot extract as password is required: {zip_path}')
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

def get_password(zipfile, passwords):
    password_ok = False
    working_password = None
    incorrect_pw = False
    if is_zip_encrypted(zipfile):
        for password in passwords:
            password_ok = verify_if_password_works(zipfile, password)

            if password_ok:
                working_password = password
                break


        if not password_ok:

            user_pw = getpass.getpass(f'\nEnter password for {zipfile} (press Enter to skip): ').strip()

            if user_pw and verify_if_password_works(zipfile, user_pw):
                print('password okay')
                passwords.append(user_pw)
                working_password = user_pw

            else:
                print('password not okay')
                incorrect_pw = True

            print('\nTip: store passwords in ENV to not have to repeat this: export ZIP_PASSWORDS=\'[\"mypass1\", \"mypass2\"]\'\n')

    else:
        print('Not encrypted: ' + zipfile)
    
    return passwords, working_password, incorrect_pw

def load_from_env_variable():
    
    passwords_json = os.getenv('ZIP_PASSWORDS', '[]')
    passwords = json.loads(passwords_json)

    return passwords

def find_zip_files(zip_patterns):
    files = []
    for pattern in zip_patterns:
        # Expand wildcards recursively
        expanded = glob.glob(pattern, recursive=True)
        files.extend(expanded)
    # Filter ZIPs and remove duplicates
    files = [f for f in set(files) if f.lower().endswith('.zip')]
    return sorted(files)

def get_password_from_env_or_prompt(source_name, unextracted_zip):
    if source_name == 'localfolder':
        passwords = load_from_env_variable()
        passwords, zip_password, incorrect_pw = get_password(unextracted_zip, passwords)
        if not incorrect_pw:
            return zip_password, unextracted_zip

        if incorrect_pw:
            return None, None

    return None, None