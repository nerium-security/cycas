'''
Utility with functions related to files to be or not to be ingested.

Provides helpers for:
    - Discovering zip files in a directory tree
    - Splitting large JSON Lines (JSONL/NDJSON) files into size-limited parts
    - Querying file sizes
    - Creating directories when needed
    - Filtering files by filename prefix and suffix
    - Deleting local files safely
'''

import os
import logging as log

log = log.getLogger(__name__)

def list_files_in_directory(directory):
    '''
    List all .zip files under a directory (recursively).

    Walks the provided directory and returns full paths for all files
    that end with '.zip'.

    Args:
        directory (str): Root directory to search.

    Returns:
        list[str]: Full paths to all discovered '.zip' files.
    '''

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

def split_jsonl_by_size(path, target_bytes=1_000_000_000, encoding='utf-8'):
    '''
    Split a JSON Lines (JSONL/NDJSON) file into multiple parts by size.

    Splits the input file into part files of approximately `target_bytes`
    without splitting lines, so each output file remains valid JSONL.

    Output part files are written in the same folder as the input file and
    named '<basename>.part<N>'.

    Args:
        path (str): Path to the JSONL file to split.
        target_bytes (int): Approximate maximum size in bytes per output part.
        encoding (str): Text encoding used for reading and writing.

    Returns:
        list[str]: List of full paths to the created part files.

    Raises:
        FileNotFoundError: If `path` does not exist or is not a file.
    '''
    
    if not os.path.isfile(path):
        raise FileNotFoundError(f'File not found: {path}')

    base = os.path.basename(path)
    folder = os.path.dirname(path) or '.'
    part_num = 1
    part_paths = []

    def _open_new_part(num):
        part_path = os.path.join(folder, f'{base}.part{num}')
        f = open(part_path, 'w', encoding=encoding, newline='\n')
        part_paths.append(part_path)
        return f, part_path

    current_size = 0
    out_f, current_part_path = _open_new_part(part_num)

    with open(path, 'r', encoding=encoding) as src:
        for line in src:
            # Size in bytes, not characters
            line_size = len(line.encode(encoding))

            # If this line would push us over the target, start a new file
            if current_size > 0 and current_size + line_size > target_bytes:
                out_f.close()
                part_num += 1
                out_f, current_part_path = _open_new_part(part_num)
                current_size = 0

            out_f.write(line)
            current_size += line_size

    out_f.close()
    return part_paths

def get_filesize_bytes(path):
    '''
    Get the size of a file in bytes.

    Args:
        path (str): Path to the file.

    Returns:
        int: File size in bytes.

    Raises:
        FileNotFoundError: If `path` does not exist or is not a file.
    '''

    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    return os.path.getsize(path)

def create_directory_if_not_exists(dest_path):
    '''
    Create a directory if it does not exist.

    Creates the directory path using `os.makedirs(..., exist_ok=True)`.
    Errors are logged and not raised.

    Args:
        dest_path (str): Directory path to create.
    '''

    try:

        if not os.path.isfile(dest_path):
            os.makedirs(dest_path, exist_ok=True)
            log.debug(f'Created directory {dest_path}')
    except Exception as e:
        log.error(f'Could not create directory: {dest_path} Error: {e}')

def filter_triage_packages(files, prefix, suffix):
    '''
    Filter a list of file paths by filename prefix and suffix.

    Only files whose basename starts with `prefix` and ends with `suffix`
    are returned.

    Args:
        files (list[str]): List of file paths to filter.
        prefix (str): Required prefix of the filename (basename).
        suffix (str): Required suffix of the filename (basename).

    Returns:
        list[str]: Filtered list of file paths that match both prefix and suffix.
    '''

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
    '''
    Delete a file from the local filesystem if it exists.

    Args:
        filepath (str): Path to the file to delete.

    Returns:
        bool: True if the file was deleted, False if the file did not exist
        or deletion failed.
    '''
    
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