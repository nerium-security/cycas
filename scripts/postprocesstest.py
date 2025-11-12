import os
import sys
import argparse
import getpass
import json
import logging as log
import glob
from datetime import datetime
from pathlib import Path
from sharedlib.utils.postprocess import (download_velociraptor, 
                                         build_remap, 
                                         find_hostname, 
                                         load_artifacts, 
                                         select_artifacts, 
                                         postprocess)

from sharedlib.utils.zip import (get_extract_path, 
                                 extract_encrypted_and_non_encrypted_zipfiles, 
                                 verify_if_password_works,
                                 is_zip_encrypted)

from sharedlib.utils.log import setup_logging, get_duration_from_timespan

script_path = sys.argv[0]
scriptname = os.path.basename(script_path)
parser = argparse.ArgumentParser(
description = 'This script post-processes zip files containing raw artefact files collected with velociraptor.',
epilog=f'Example usage: python3 {scriptname} -i *.zip --outputtype csv')
parser.add_argument('-o', '--outputtype', default='csv', choices=['csv', 'jsonl', 'json'])
parser.add_argument('-c', '--custom_definitions', default='../velociraptor/definitions/')
parser.add_argument('-b', '--binary', default='../velociraptor/velociraptor')
parser.add_argument('-u', '--url', default='https://github.com/Velocidex/velociraptor/releases/download/v0.74/velociraptor-v0.74.5-linux-amd64')
parser.add_argument('-i', '--input', nargs='+',  help='Path(s) to ZIP file(s). Supports wildcards (e.g. *.zip or **/*.zip)')
parser.add_argument('-f', '--outputfolder', help='If not set it will output next to the zip file.')
parser.add_argument('-a', '--artifacts', default='../velociraptor/artifacts/velociraptor_artifacts.json')
parser.add_argument('-e', '--essentials', help='Only essential artifacts', action='store_true' )
parser.add_argument('-v', '--verbose', help='Enables verbose logging', action='store_true')
args = parser.parse_args()

binary = args.binary
url = args.url
inputpath = args.input
custom_definitions = args.custom_definitions
outputtype = args.outputtype
verbose = args.verbose
artifacts = args.artifacts
essentials = args.essentials
outputfolder = args.outputfolder

log = log.getLogger(__name__)

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

def main(): 

    scriptlocation = os.path.dirname(os.path.realpath(__file__))
    definitions = os.path.join(scriptlocation, custom_definitions)
    binary_fullpath = os.path.join(scriptlocation, binary)
    logpath = os.path.join(scriptlocation, '../logs')
    start = datetime.now()

    loglevel = 'DEBUG' if verbose else 'INFO'
    setup_logging(logpath, loglevel)

    log.info('Script started.')

    zipfiles = find_zip_files(inputpath)
    if len(zipfiles) == 0:
        log.info('Found 0 zipfiles.')
        log.info('Exiting script')
        exit()

    download_velociraptor(binary_fullpath, url)

    passwords = load_from_env_variable()

    essentials_or_full = 'essential' if essentials else 'full'
    artifacts_fullpath = os.path.join(scriptlocation, artifacts)
    artifacts_json = load_artifacts(artifacts_fullpath)
    artifacts_selected = select_artifacts(artifacts_json, essentials_or_full)

    for zipfile in zipfiles:
        zipfile = str(zipfile)

        if outputfolder:
            p = Path(zipfile)
            pathswap = Path(outputfolder) / p.name
        else:
            pathswap = zipfile
        extract_path = os.path.splitext(pathswap)[0]

        passwords, zip_password, incorrect_pw = get_password(zipfile, passwords)

        if incorrect_pw:
            continue
        
        extracted_zip = extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password)

        remappingfile = build_remap(extracted_zip, 
                                    extract_path,
                                    binary_fullpath,
                                    definitions,
                                    extract_path)

        hostname = find_hostname(remappingfile,
                                 binary_fullpath,
                                 definitions)

        for artifact in artifacts_selected:

            postprocessed = postprocess(hostname, 
                                        artifact, 
                                        zipfile, 
                                        definitions, 
                                        extract_path, 
                                        binary_fullpath, 
                                        outputtype,
                                        remappingfile)

            if postprocessed:
                log.info(f'Output written to: {postprocessed}')
            
        duration = get_duration_from_timespan(start)
        log.info(f'Script finished in: {duration}')


if __name__ == '__main__':
    main()