'''
Utilities for downloading and running Velociraptor for artifact post-processing.

Provides helpers to:
    - Download the Velociraptor binary and set executable permissions
    - Build and run Velociraptor query commands
    - Generate a remapping file for reading zip contents
    - Extract a hostname from a remapped registry hive
    - Load and select artifact sets from a JSON configuration
    - Run post-processing for a single artifact and return structured results

Most functions log errors rather than raising exceptions, and several
functions create directories or write output files as side effects.
'''

import logging as log
import os
import requests
import subprocess
import stat
import json
import re
import time
import shlex
from pathlib import Path
from core.utils.files import create_directory_if_not_exists
from core.utils.summary import define_results_postprocess_dict

log = log.getLogger(__name__)

def set_executepermissions(dest_path):
    '''
    Set executable permissions for the Velociraptor binary.

    Adds the execute bit to the file mode of the given path.

    Args:
        dest_path (str): Path to the Velociraptor binary.
    '''

    try:
        os.chmod(dest_path, os.stat(dest_path).st_mode | stat.S_IEXEC)
        log.debug(f'Set execute permission for {dest_path}')
    except Exception as e:
        log.error(f'Could not set execute permission for {dest_path}. Error: {e}')

def run_command(cmd, result, store_output, max_duration_sec=400):
    '''
    Run a subprocess command and record execution results.

    Executes the provided command using `subprocess.run(check=True)` and
    writes execution metadata into the provided `result` dictionary. Sets
    a maximum duration on how long this process runs.

    Args:
        cmd (list[str]): Command and arguments to execute.
        result (dict): Mutable dictionary updated with execution results.
        store_output (bool): If True, captures stdout and stderr. If False,
            captures stderr only.
        max_duration_sec: Sets a maximum duration in seconds

    Returns:
        dict: The updated result dictionary containing keys such as:
            - success (bool)
            - duration_in_sec (float)
            - stdout (str or None)
            - stderr (str or None)
            - returncode (int)
            - error (str) on failure
    '''

    if store_output:
        run_opts = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
    else:
        run_opts = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
            "text": True,
        }

    start = time.time()

    try:
        log.debug(f'Running command: {" ".join(cmd)}')

        cp = subprocess.run(cmd, check=True, timeout=max_duration_sec, **run_opts)

        duration = time.time() - start

        result['success'] = True
        result['duration_in_sec'] = duration
        result['stdout'] = cp.stdout if store_output else None
        result['stderr'] = cp.stderr
        result['returncode'] = cp.returncode

        log.info(f'Command executed successfully in {round(duration, 2)} seconds.')

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:

        duration = time.time() - start

        stdout = getattr(e, 'stdout', None)
        stderr = getattr(e, 'stderr', None)
        returncode = getattr(e, 'returncode', None)

        result['success'] = False
        result['duration_in_sec'] = duration
        result['error'] = (stderr or stdout or str(e)).strip()
        result['stdout'] = stdout
        result['stderr'] = stderr
        result['returncode'] = returncode

        if isinstance(e, subprocess.TimeoutExpired):
            msg = f'Command exceeded the configured maximum duration of {max_duration_sec} seconds'
            log.error(msg)
            result['stderr'] = msg
        else:
            log.error(f'Command failed (exit {returncode}) after {duration} seconds.')        

    return result

def download_velociraptor(binary, url):
    '''
    Download the Velociraptor binary if it does not already exist.

    Fetches the binary from the provided URL and writes it to `binary`.
    If the destination file already exists, no download is performed.

    Args:
        binary (str): Destination path for the Velociraptor binary.
        url (str): Download URL for the Velociraptor binary.
    '''

    if os.path.exists(binary):
        log.info(f'Download is not required of {url} as {binary} already exists.')
        return

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(binary, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        set_executepermissions(binary)
        
        log.info(f'Downloaded {url} -> {binary}')
    except Exception as e:
        log.error(f'Could not download {url} to {binary}. Error: {e}')

def build_remap(zipfile, remapfolder, binary, definitions, unzipdir):
    '''
    Build a Velociraptor remapping file for processing a zipfile.

    Creates the remapping directory if needed and runs a Velociraptor query
    to generate a remapping YAML file that can be used with '--remap' to
    access zip contents.

    Args:
        zipfile (str): Path to the zipfile to remap.
        remapfolder (str): Directory where remap-related artifacts may be stored.
        binary (str): Path to the Velociraptor binary.
        definitions (str): Path to Velociraptor artifact definitions.
        unzipdir (str): Base directory for unzip/output files.

    Returns:
        str: Full path to the generated remapping YAML file.
    '''

    create_directory_if_not_exists(remapfolder)

    unzip_dir_fullpath = get_zipfiledir(zipfile, unzipdir)

    remappingfilename = '_remapping.yaml'

    cmd = [
        binary,
        f'--definitions={definitions}',
        'query',
        f"SELECT * FROM Artifact.Custom.Generic.Utils.ZipRemap(ImagePath=\'{zipfile}\', Upload=\'Y\', Remappingfilename=\'{remappingfilename}\')",
        "--dump_dir",f"{unzip_dir_fullpath}"
    ]

    remappingfile = os.path.join(unzip_dir_fullpath, remappingfilename)

    log.info(f'Creating remapping file: {remappingfile}')
    run_command(cmd, {}, store_output=True)
    
    return remappingfile

def write_command_to_logfile(results, extract_path, logfilename):
    '''
    Writes the executed command to a logfile with timestamp.

    Args:
        cmd (list | str): Command passed to subprocess.
        extract_path (str): Path to extraction location of zip
        logfilename (str): logfilename.
    '''

    cmd = results.get('cmd')
    out_path = os.path.join(extract_path, logfilename)

    # Convert list to properly escaped shell string
    if isinstance(cmd, (list, tuple)):
        command_str = shlex.join(cmd)
    else:
        command_str = str(cmd)

    with open(out_path, 'a', encoding='utf-8') as f:
        f.write(f'{command_str}\n\n')

def find_hostname(remappingfile, binary, definitions):
    '''
    Extract the hostname from a remapped SYSTEM registry hive.

    Uses the provided Velociraptor remapping file to query a registry key
    that typically contains the Windows computer name.

    Args:
        remappingfile (str): Path to the Velociraptor remapping YAML file.
        binary (str): Path to the Velociraptor binary.
        definitions (str): Path to Velociraptor artifact definitions.

    Returns:
        str: Extracted hostname if available, otherwise an empty string.
    '''

    registrykey = "HKEY_LOCAL_MACHINE//SYSTEM//ControlSet001//Control//ComputerName//ComputerName//ComputerName"

    cmd = [
        binary,
        '--remap', f'{remappingfile}',
        '--nobanner',
        '--definitions', f'{definitions}',
        'query', 
        f"SELECT Data.value as Hostname FROM glob(globs=\'{registrykey}\', accessor=\'registry\')"
    ]

    log.info(f'Extracting hostname in {registrykey}')
    result = run_command(cmd, {}, store_output=True)
    try:

        hostname = json.loads(result['stdout'])[0]['Hostname']
        log.info(f'Successfully extracted hostname: {hostname}')
        return hostname
    except:
        log.info(f'Could not extract hostname from registry key.')
        return ''

def load_artifacts(artifactslist):
    '''
    Load the Velociraptor artifact configuration from a JSON file.

    Args:
        artifactslist (str): Path to the JSON file containing artifact groups.

    Returns:
        dict or None: Parsed JSON content if successful, otherwise None.
    '''

    try:
        with open(artifactslist, 'r') as f:
            artifacts = json.load(f)    
            log.info(f'Successfully loaded artifact list from {artifactslist}')
        return artifacts
    except Exception as e:
        log.error(f'Could not load artifact list. Error: {e}')

        return None

def select_artifacts(artifacts, postprocess_var):
    '''
    Select the artifacts to run based on a post-processing mode.

    Supports selecting:
        - 'essential': only the essential artifact list
        - 'full': essential plus full artifact list
        - any other value: empty selection

    Ensures items are unique while preserving the original order.

    Args:
        artifacts (dict): Artifact configuration containing 'essential' and 'full' lists.
        postprocess_var (str): Mode selector (e.g. 'essential' or 'full').

    Returns:
        list[str]: Ordered list of unique artifact names to run.
    '''

    essentials = artifacts['essential']
    full = artifacts['full']

    seen = set()
    result = []

    if postprocess_var == 'full':
        items = essentials + full
    elif postprocess_var == 'essential':
        items = essentials
    else:
        items = []

    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)

    return result

def get_zipfilename(zipfile, unzip_dir):
    '''
    Derive a zipfile basename used for output metadata.

    If the zipfile path starts with the unzip directory, removes that prefix.
    Otherwise falls back to `os.path.basename(zipfile)`.

    Args:
        zipfile (str): Zipfile path.
        unzip_dir (str): Base unzip directory path.

    Returns:
        str: Derived zipfile basename.
    '''

    if zipfile.startswith(unzip_dir):
        zipfile_basename = zipfile.removeprefix(unzip_dir)
    else:
        zipfile_basename = os.path.basename(zipfile)

    return zipfile_basename

def get_zipfiledir(zipfile, unzip_dir):
    '''
    Determine and create the output directory for a zipfile.

    Computes a directory path under `unzip_dir` based on the zipfile name,
    normalizes it by removing a trailing '/data' suffix, and ensures the
    directory exists.

    Args:
        zipfile (str): Zipfile path.
        unzip_dir (str): Base directory for unzip/output files.

    Returns:
        str: Full path to the directory used for extracted and generated files.
    '''

    if zipfile.startswith(unzip_dir):
        unzip_dir_fullpath = os.path.splitext(zipfile)[0]
    else:
        zipfile_noext = Path(zipfile).stem
        unzip_dir_temp = os.path.join(unzip_dir, zipfile_noext)
        if unzip_dir_temp.endswith(zipfile_noext):
            unzip_dir_fullpath = unzip_dir
        else:
            unzip_dir_fullpath = unzip_dir_temp

    unzip_dir_fullpath = unzip_dir_fullpath.removesuffix('/data')

    os.makedirs(unzip_dir_fullpath, exist_ok=True)

    return unzip_dir_fullpath

def postprocess(hostname, artifact, zipfile, definitions, unzipdir, binary, outputformat, remappingfile, dur):
    '''
    Run Velociraptor post-processing for a single artifact against a zipfile.

    Builds a Velociraptor query command that executes the specified artifact
    and writes results to an output file in the chosen format. A separate log
    file is also written. Returns a structured results dictionary including
    execution success, duration, output size, and paths.

    Args:
        hostname (str): Hostname value to embed in each output row.
        artifact (str): Velociraptor artifact query target (e.g. 'Custom.Windows...()').
        zipfile (str): Path to the zipfile being processed.
        definitions (str): Path to Velociraptor artifact definitions.
        unzipdir (str): Base output directory for extracted/generated files.
        binary (str): Path to the Velociraptor binary.
        outputformat (str): Output format passed to Velociraptor (e.g. 'jsonl', 'csv').
        remappingfile (str): Path to the Velociraptor remapping YAML file.

    Returns:
        dict: Post-processing results dictionary containing keys such as:
            - success (bool)
            - duration_in_sec (float)
            - stderr (str or None)
            - returncode (int)
            - size (int)
            - fullpath (str)
            - artifact (str)
            - basename (str)
    '''
    
    postprocess_results = define_results_postprocess_dict()

    artifact_name = re.sub(r'\(.*', '', artifact)

    zipfile_basename = get_zipfilename(zipfile, unzipdir)

    unzip_dir_fullpath = get_zipfiledir(zipfile, unzipdir)

    outputfile = os.path.join(unzip_dir_fullpath, artifact_name + '.' + outputformat)
    logfile = os.path.join(unzip_dir_fullpath, artifact_name + '.log')

    cmd = [
        binary,
        '--remap', f'{remappingfile}',
        '--nobanner',
        '--definitions', f'{definitions}',
        'query', 
        f"SELECT *, \'{hostname}\' as Hostname, \'{zipfile_basename}\' as Sourcefile FROM Artifact.{artifact}",
        '--format', f'{outputformat}',
        '--output', f'{outputfile}',
        '--logfile', f'{logfile}'
    ]

    log.info(f'Running artifact: {artifact}')

    postprocess_results = run_command(cmd, postprocess_results, False, dur)

    filesize = os.path.getsize(outputfile)
    if os.path.exists(outputfile) and filesize == 0:
        log.debug(f'Empty file: {outputfile}')

    postprocess_results['cmd'] = cmd
    postprocess_results['size'] = filesize
    postprocess_results['fullpath'] = outputfile
    postprocess_results['artifact'] = artifact
    postprocess_results['basename'] = os.path.basename(outputfile)

    return postprocess_results