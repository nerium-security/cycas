'''
ZIP handling utilities for the ingestion pipeline.

Provides helpers for:
    - Detecting encrypted ZIP files (AES/standard ZIP encryption)
    - Extracting hostnames from ZIP filenames
    - Validating ZIP passwords and prompting the user when needed
    - Listing and extracting files from ZIP archives (including nested data.zip)
    - Applying ignore rules to ZIP contents (by extension, size, and patterns)
    - Discovering ZIP files on disk using glob patterns

Several functions log errors instead of raising exceptions and some may
prompt the user for input when a password is required.
'''

from core.utils.files import list_files_in_directory, list_files_in_directory, filter_triage_packages
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
    '''
    Check whether a ZIP archive contains encrypted entries.

    Args:
        zipfile (str): Path to the ZIP file.

    Returns:
        bool: True if the ZIP contains an encrypted entry, otherwise False.
    '''
    
    try:
        with pyzipper.AESZipFile(zipfile) as zf:

            for info in zf.infolist():

                if info.flag_bits & 0x1 or getattr(info, 'is_encrypted', False):
                    return True
                else:
                    return False
    except:
        return None

def get_hostname_from_filename(fullpath):
    ''' 
    Extracts the hostname from filename using regex. 
    
    Examples of hostnames that are extracted:
    
    - Collection-HOSTNAME-2024-03-01T16_10_46Z.zip
    - HOSTNAME-2025-03-01T16_10_46Z.zip
    - LAPTOP-DC-C.65e548a6aa01faa1-F.D3DN1LABPD0OA

    Args:
        fullpath (str): Filename or full path to parse.

    Returns:
        str: Extracted hostname if matched, otherwise an empty string.
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
    '''
    Verify whether a password can decrypt at least one entry in an encrypted ZIP.

    Attempts to open an entry using the provided password and read a single byte.

    Args:
        zipfile (str): Path to the ZIP file.
        zip_password (str): Candidate password.

    Returns:
        bool: True if the password successfully decrypts an entry, otherwise False.
    '''

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
    '''
    Determine whether a ZIP contains raw artifacts under the 'uploads/' prefix.

    Args:
        content (list): List of ZipInfo-like objects returned from `infolist()`.

    Returns:
        bool: True if any entry path starts with 'uploads/', otherwise False.
    '''

    for file_in_zip in content:

        if file_in_zip.filename.startswith('uploads/'):
            log.info('Starts post-processing as zipfile seems to contain raw artifacts in \'uploads\' folder')
            
            return True
    
    log.info('Skipping post-processing as zipfile does not '
             'seem to contain raw artifacts typically stored in \'uploads\' folder')
    return False

def extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password):
    '''
    Handle extraction logic for encrypted and non-encrypted triage ZIP structures.

    Lists ZIP contents and detects whether a nested 'data.zip' exists. If found,
    extracts 'data.zip' to the extraction path and returns it as the new archive
    to process. If no nested archive is present, returns the original ZIP.

    Args:
        zipfile (str): Path to the outer ZIP file.
        extract_path (str): Directory to extract into.
        zip_password (str | None): ZIP password, if required.

    Returns:
        tuple[str | None, str | None]: Tuple of:
            - extracted_zip: Path to extracted nested 'data.zip' if present, otherwise None
            - unextracted_zip: Path to the ZIP to process if no nested ZIP was extracted, otherwise None
    '''

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
    '''
    Build an extraction folder path for a zipfile.

    Uses the zip filename (without extension) as a subfolder under the
    provided base extraction directory.

    Args:
        zip_path (str): Path to the zipfile.
        base_extract_dir (str): Base directory for extractions.

    Returns:
        str: Full path to the extraction directory for this zipfile.
    '''

    zip_name = os.path.splitext(os.path.basename(zip_path))[0]

    return os.path.join(base_extract_dir, zip_name)

def extract_single_file(zip_path, file_info, extract_to, password=None):
    '''
    Extract a single entry from a ZIP archive.

    Extracts the entry described by `file_info` into the target folder. If the
    entry is encrypted, a password must be provided.

    Args:
        zip_path (str): Path to the ZIP archive.
        file_info: Zip entry metadata (AESZipInfo/ZipInfo-like object).
        extract_to (str): Destination directory for extraction.
        password (str | None): Password for encrypted entries.

    Returns:
        str or None: Full path to the extracted file on success, otherwise None.
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
    '''
    Determine whether a ZIP entry should be ignored for further processing.

    Applies rules in this order:
        - Ignore directories
        - Ignore non-.json/.jsonl files
        - Ignore empty files
        - Ignore files matching basename patterns in ignorelist['ignorelist']
        - Ignore files matching path patterns in ignorelist['ignorepattern']

    Args:
        file_in_zip: Zip entry metadata object with `filename` and `file_size`.
        ignorelist (dict): Ignore configuration with optional keys:
            - 'ignorelist': list of filename (basename) patterns
            - 'ignorepattern': list of full path patterns

    Returns:
        dict: Decision payload
    '''
    
    filename = file_in_zip.filename

    def result(ignored, reason=None, pattern=None):

        if ignored:
            log.debug(f'Ignoring: {file_in_zip.filename} | reason={reason} | pattern={pattern}')
        return {
            'basename': os.path.basename(filename),
            'ignored_upload': ignored,
            'ignored_reason': reason,
            'ignored_pattern': pattern,
            'upload_initiated': False
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
    '''
    Load ignore patterns from a JSON file.

    Args:
        ignorelist_path (str): Path to the ignore list JSON file.

    Returns:
        dict or list: Parsed ignore list configuration on success.
        Returns an empty list on failure.
    '''

    try:
        with open(ignorelist_path, 'r') as f:
            config = json.load(f)
            log.debug(f'Loaded ignore list from {ignorelist_path}', )
            return config
    except Exception as e:
        log.error(f'Could not load {ignorelist_path}. Error: {str(e)}')
        return []

def list_files_in_zip(zip_path, password=None):
    '''
    List entries contained in a ZIP archive.

    If the archive appears to contain encrypted entries and no password is
    provided, returns an empty list.

    Args:
        zip_path (str): Path to the ZIP archive.
        password (str | None): Password for encrypted archives.
    '''

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
    '''
    Extract a computer name from a filename of the form 'Collection-<name>-YYYY-MM-DD'.

    Args:
        filename (str): Filename to parse.

    Returns:
        str: Extracted computer name if matched, otherwise 'ComputernameNotFound'.
    '''

    match = re.search(r'Collection-(.*)-\d{4}-\d{2}-\d{2}', filename)

    if match:
        computer_name = match.group(1)
        log.info(f'Computer name: {computer_name}')
        return computer_name
    else:
        log.warning('Computer name not found.')
        return 'ComputernameNotFound'

def get_password(zipfile, passwords):
    '''
    Determine a working ZIP password from a list or by prompting the user.

    If the ZIP is encrypted, tries each password in `passwords`. If none work,
    prompts the user for a password using `getpass`. If the user provides a
    working password, it is appended to the list.

    Args:
        zipfile (str): Path to the ZIP file.
        passwords (list[str]): Candidate passwords to try.

    Returns:
        tuple[list[str], str | None, bool]: Tuple of:
            - passwords: Updated password list (may include newly entered password)
            - working_password: Password that works, or None
            - incorrect_pw: True if a password was entered but did not work
    '''

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
    '''
    Load ZIP passwords from the 'ZIP_PASSWORDS' environment variable.

    Expects a JSON-encoded list of passwords, for example:
        '["pass1", "pass2"]'

    Returns:
        list[str]: List of passwords loaded from the environment.
    '''

    passwords_json = os.getenv('ZIP_PASSWORDS', '[]')
    passwords = json.loads(passwords_json)

    return passwords

def find_zip_files(zip_patterns):
    '''
    Find ZIP files matching one or more glob patterns.

    Expands patterns (with optional recursive wildcards), collects matches,
    filters to '.zip' files, removes duplicates, and returns a sorted list.

    Args:
        zip_patterns (list[str]): Glob patterns to expand.

    Returns:
        list[str]: Sorted list of unique ZIP file paths.
    '''

    files = []
    for pattern in zip_patterns:
        # convert relative path to absolute path
        pattern = os.path.abspath(pattern)

        # Expand wildcards recursively
        expanded = glob.glob(pattern, recursive=True)
        files.extend(expanded)

    # Filter ZIPs and remove duplicates
    files = [f for f in set(files) if f.lower().endswith('.zip')]
    return sorted(files)

def get_password_from_env_or_prompt(source_name, unextracted_zip):
    '''
    Resolve a ZIP password for a given source, using env passwords or prompting.

    For source 'localfolder', loads candidate passwords from the environment and
    prompts the user if none work. For other sources, returns (None, None).

    Args:
        source_name (str): Source identifier (e.g. 'localfolder').
        unextracted_zip (str): Path to the zipfile that may require a password.

    Returns:
        tuple[str | None, str | None]: Tuple of:
            - zip_password: Working password if available, otherwise None
            - zip_path: Zipfile path if processing can proceed, otherwise None
    '''

    if source_name == 'localfolder':
        passwords = load_from_env_variable()
        passwords, zip_password, incorrect_pw = get_password(unextracted_zip, passwords)
        if not incorrect_pw:
            return zip_password, unextracted_zip

        if incorrect_pw:
            return None, None

    return None, None

def list_zipfiles(managers, source_name, Config):
    '''
    List and filter ZIP files from a configured data source.

    Retrieves files from the specified source (blob storage, SAS container,
    SFTP server, or local filesystem) and filters the results using the
    configured ZIP filename prefix and suffix.

    Args:
        managers: Container holding authenticated service managers
            for the enabled data sources.
        source_name (str): Name of the data source to query.
            Supported values: 'blob', 'sas', 'sftp', 'localfolder'.
        Config: contains inputparameters from .env file

    Returns:
        list[str]: List of ZIP file paths or names matching the configured
        prefix and suffix.
    '''

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