import logging as log
import os
import requests
import subprocess
import stat
import json
import re
import time
from pathlib import Path
from ..utils.config import load_config

Config = load_config()
binary = Config.velociraptor_binary
remapfolder = Config.velociraptor_remappingdir
definitions = Config.velociraptor_definitions
outputformat = Config.velociraptor_outputformat
artifactslist = Config.velociraptor_artifactslist
url = Config.velociraptor_url
postprocess_var = Config.velociraptor_postprocess
unzip_dir = Config.var_unzip_directory

def set_executepermissions(dest_path):
    '''Sets the execution permissions for the Velocriaptor binary.'''

    try:
        os.chmod(dest_path, os.stat(dest_path).st_mode | stat.S_IEXEC)
        log.debug(f'Set execute permission for {dest_path}')
    except Exception as e:
        log.error(f'Could not set execute permission for {dest_path}. Error: {e}')

def create_directory_if_not_exists(dest_path):
    '''Creates the directory for Velociraptor binary if it does not exists.'''

    folder = os.path.dirname(dest_path)
    try:
        if not os.path.isfile(folder):
            os.makedirs(folder, exist_ok=True)
            log.debug(f'Created directory {folder}')
    except Exception as e:
        log.error(f'Could not create directory: {folder} Error: {e}')

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
        log.info(f'Running command: {" ".join(cmd)}')
        start = time.time()

        cp = subprocess.run(cmd, check=True, **run_opts)
        end = time.time()
        duration = end - start
        log.info(f'Command executed successfully in {duration:.2f} seconds.')
        return cp
    except subprocess.CalledProcessError as e:
        log.info(f'Command failed to execute. Error: {e.returncode}')
        return None

def download_velociraptor():
    '''This function downloads the velociraptor binary if it does not exist on disk yet.'''

    create_directory_if_not_exists(binary)

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


def build_remap(zipfile):
    '''
    This function builds the Velociraptor remapping file to be able to
    easily post-process the results of the Windows.KapeFiles.Targets artifact
    '''

    create_directory_if_not_exists(remapfolder)

    unzip_dir_fullpath = get_zipfiledir(zipfile)
    
    remappingfilename = 'remapping.yaml'

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

def find_hostname(remappingfile):
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
    result = run_command(cmd, store_output=True)
    try:
        hostname = json.loads(result.stdout)[0]['Hostname']
        log.info(f'Successfully extracted hostname: {hostname}')
        return hostname
    except:
        log.info(f'Could not extract hostname from registry key.')


def load_artifacts():
    ''' Loads the Velociraptor artefacts from the inputfile.'''

    try:
        with open(artifactslist, 'r') as f:
            artifacts = json.load(f)    
            log.info(f'Successfully loaded artifact list from {artifactslist}')
        return artifacts
    except Exception as e:
        log.error(f'Could not load artifact list. Error: {e}')

        return None

def select_artifacts(artifacts):
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

def get_zipfilename(zipfile):
    '''Gets the zip file basename '''

    if zipfile.startswith(unzip_dir):
        zipfile_basename = zipfile.removeprefix(unzip_dir)
    else:
        zipfile_basename = os.path.basename(zipfile)

    

    return zipfile_basename

def get_zipfiledir(zipfile):
    '''Gets the zipfile directory'''

    if zipfile.startswith(unzip_dir):
        unzip_dir_fullpath = os.path.splitext(zipfile)[0]
    else:
        zipfile_noext = Path(zipfile).stem
        unzip_dir_fullpath = os.path.join(unzip_dir, zipfile_noext)

    os.makedirs(unzip_dir_fullpath, exist_ok=True)

    return unzip_dir_fullpath

def postprocess(hostname, artifact, zipfile):
    '''Prepares the Velociraptor command and runs it to post-process zip file.'''

    artifact_name = re.sub(r'\(.*', '', artifact)

    zipfile_basename = get_zipfilename(zipfile)

    unzip_dir_fullpath = get_zipfiledir(zipfile)

    outputfile = os.path.join(unzip_dir_fullpath, artifact_name + '.' + outputformat)
    logfile = os.path.join(unzip_dir_fullpath, artifact_name + '.log')

    cmd = [
        binary,
        '--remap', f'{unzip_dir_fullpath}/remapping.yaml',
        '--nobanner',
        '--definitions', f'{definitions}',
        'query', 
        f"SELECT *, \'{hostname}\' as Hostname, \'{zipfile_basename}\' as Sourcefile FROM Artifact.{artifact}",
        '--format', f'{outputformat}',
        '--output', f'{outputfile}',
        '--logfile', f'{logfile}'
    ]

    run_command(cmd)

    if os.path.exists(outputfile) and os.path.getsize(outputfile) == 0:
        log.debug(f'Empty file: {outputfile}')
    else:
        return outputfile