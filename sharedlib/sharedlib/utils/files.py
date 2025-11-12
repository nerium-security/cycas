import os
import logging as log

log = log.getLogger(__name__)

def list_files_in_directory(directory):
    '''Lists all the root files in the provided directory path'''

    log.info(f'Listing files in: {directory}')

    file_list = []
    for subdir, dirs, files in os.walk(directory):
        for file in files:
            fullpath = os.path.join(subdir, file)
            if fullpath.endswith('.zip'):
                file_list.append(fullpath)
                log.debug('Found file %s' %file)
    log.info(f'Found {len(file_list)} zip file(s).')
    return file_list

def create_directory_if_not_exists(dest_path):
    '''Creates a directory when it does not exist'''

    try:

        if not os.path.isfile(dest_path):
            os.makedirs(dest_path, exist_ok=True)
            log.debug(f'Created directory {dest_path}')
    except Exception as e:
        log.error(f'Could not create directory: {dest_path} Error: {e}')

def filter_triage_packages(files, prefix, suffix):
    '''Returns files that start with a specific prefix and suffix.'''

    log.debug('Filter prefix: %s' %prefix)
    log.debug('Filter suffix: %s' %suffix)
    file_list = []
    for file in files:
        file_basename = os.path.basename(file)

        if file_basename.startswith(prefix) and file_basename.endswith(suffix):
            file_list.append(file)
            log.debug('Found triage package: %s' %file)

    if len(file_list) == 0:
        log.info('Found no files.')

    return file_list

def delete_file(filepath):
    '''Delete a single file if it exists.'''
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
            log.debug(f'Deleted file: {filepath}')
            return True
        else:
            log.warning(f'File not found or not a regular file: {filepath}')
            return False
    except Exception as e:
        log.error(f'Failed to delete {filepath}: {e}')
        return False