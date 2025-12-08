import logging as log
import os
import requests
import subprocess
import stat
import json
import re
import time
from pathlib import Path
from sharedlib.utils.files import create_directory_if_not_exists
from sharedlib.utils.misc import calculate_total, adding_seconds

log = log.getLogger(__name__)

def set_executepermissions(dest_path):
    '''Sets the execution permissions for the Velocriaptor binary.'''

    try:
        os.chmod(dest_path, os.stat(dest_path).st_mode | stat.S_IEXEC)
        log.debug(f'Set execute permission for {dest_path}')
    except Exception as e:
        log.error(f'Could not set execute permission for {dest_path}. Error: {e}')

def run_command(cmd, store_output=False):
    '''Runs the Velociraptor command to post-process zip file.'''

    if store_output == True:
        run_opts = {
            "capture_output": True,
            "text": True
        }
    else:
        run_opts = {
            "capture_output": False,
            "text": False
        }
    
    try:
        log.debug(f'Running command: {" ".join(cmd)}')
        start = time.time()

        cp = subprocess.run(cmd, check=True, **run_opts)
        end = time.time()

        duration = end - start
        
        log.info(f'Command executed successfully in {duration:.2f} seconds.')

    except subprocess.CalledProcessError as e:
        log.info(f'Command failed to execute. Error: {e.returncode}')
        cp = None
        duration = None
    
    return cp, duration

def collecting_data_for_summary(duration):
    ''' Returns value with only 2 decimals after comma '''

    return round(duration, 2)

def generate_summary_postprocessing(summary, zipfile, extracted_zip, filename):
    ''' Generates a summary of post-processing time and dumps it to stdout and a file '''

    summary[zipfile]['total'] = calculate_total(summary[zipfile])
    summary[zipfile] = adding_seconds(summary[zipfile])
    summary_json = json.dumps(summary, indent=4)

    log.info('Printing summary:')
    print('\n', summary_json, '\n')

    fullpath = os.path.join(extracted_zip, filename)
    log.info(f'Outputting summary to: {fullpath}')
    with open(fullpath, 'w') as f:
        f.write(summary_json)


def download_velociraptor(binary, url):
    '''This function downloads the velociraptor binary if it does not exist on disk yet.'''

    #create_directory_if_not_exists(binary)

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
    This function builds the Velociraptor remapping file to be able to
    easily post-process the results of the Windows.KapeFiles.Targets artifact
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

    run_command(cmd, store_output=True)
    
    return remappingfile

def find_hostname(remappingfile, binary, definitions):
    '''
    Outputs the hostname by using the generate remapping file to read the zip-file
    containg the SYSTEM registry hive with the ComputerName value.
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
    result, _ = run_command(cmd, store_output=True)
    try:
        hostname = json.loads(result.stdout)[0]['Hostname']
        log.info(f'Successfully extracted hostname: {hostname}')
        return hostname
    except:
        log.info(f'Could not extract hostname from registry key.')
        return ''


def load_artifacts(artifactslist):
    ''' Loads the Velociraptor artefacts from the inputfile.'''

    try:
        with open(artifactslist, 'r') as f:
            artifacts = json.load(f)    
            log.info(f'Successfully loaded artifact list from {artifactslist}')
        return artifacts
    except Exception as e:
        log.error(f'Could not load artifact list. Error: {e}')

        return None

def select_artifacts(artifacts, postprocess_var):
    '''Selects the Velociraptor artifacts that need to be launched'''

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
    '''Gets the zip file basename '''

    if zipfile.startswith(unzip_dir):
        zipfile_basename = zipfile.removeprefix(unzip_dir)
    else:
        zipfile_basename = os.path.basename(zipfile)

    return zipfile_basename

def get_zipfiledir(zipfile, unzip_dir):
    '''Gets the zipfile directory'''

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

def postprocess(hostname, artifact, zipfile, definitions, unzipdir, binary, outputformat, remappingfile):
    '''Prepares the Velociraptor command and runs it to post-process zip file.'''
    
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
    _, duration = run_command(cmd)

    if os.path.exists(outputfile) and os.path.getsize(outputfile) == 0:
        log.debug(f'Empty file: {outputfile}')

    return outputfile, duration