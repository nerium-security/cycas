import os
import sys
import argparse
import logging as log
import json
from datetime import datetime
from pathlib import Path
from sharedlib.utils.postprocess import (download_velociraptor, 
                                         build_remap, 
                                         find_hostname, 
                                         load_artifacts, 
                                         select_artifacts, 
                                         postprocess,
                                         generate_summary_postprocessing,
                                         collecting_data_for_summary)

from sharedlib.utils.zip import (extract_encrypted_and_non_encrypted_zipfiles, 
                                 get_password,
                                 load_from_env_variable,
                                 find_zip_files)

from sharedlib.utils.log import setup_logging
from sharedlib.utils.misc import get_duration_from_timespan

script_path = sys.argv[0]
scriptname = os.path.basename(script_path)
parser = argparse.ArgumentParser(
description = 'This script post-processes zip files containing raw artefact files collected with velociraptor.',
epilog=f'Example usage: python3 {scriptname} -i *.zip')
parser.add_argument('-o', '--outputtype', default='csv', choices=['csv', 'jsonl', 'json'])
parser.add_argument('-c', '--custom_definitions', default='../velociraptor/definitions/')
parser.add_argument('-b', '--binary', default='../velociraptor/velociraptor')
parser.add_argument('-u', '--url', default='https://github.com/Velocidex/velociraptor/releases/download/v0.74/velociraptor-v0.74.5-linux-amd64')
parser.add_argument('-i', '--input', nargs='+',  help='Path(s) to ZIP file(s). Supports wildcards (e.g. *.zip or **/*.zip)')
parser.add_argument('-f', '--outputfolder', help='If not set it will output next to the zip file.')
parser.add_argument('-a', '--artifacts', default='../velociraptor/artifacts/velociraptor_artifacts.json')
parser.add_argument('-e', '--essentials', help='Only essential artifacts', action='store_true' )
parser.add_argument('-v', '--verbose', help='Enables verbose logging', action='store_true')
args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])

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
    summary = {}

    for zipfile in zipfiles:
        zipfile = str(zipfile)
        summary[zipfile] = {}

        if outputfolder:
            p = Path(zipfile)
            pathswap = Path(outputfolder) / p.name
        else:
            pathswap = zipfile
        extract_path = os.path.splitext(pathswap)[0]

        passwords, zip_password, incorrect_pw = get_password(zipfile, passwords)

        if incorrect_pw:
            continue
        
        extracted_zip, _ = extract_encrypted_and_non_encrypted_zipfiles(zipfile, extract_path, zip_password)

        remappingfile = build_remap(extracted_zip, 
                                    extract_path,
                                    binary_fullpath,
                                    definitions,
                                    extract_path)

        hostname = find_hostname(remappingfile,
                                 binary_fullpath,
                                 definitions)

        for artifact in artifacts_selected:

            postprocessed, duration = postprocess(hostname, 
                                        artifact, 
                                        zipfile, 
                                        definitions, 
                                        extract_path, 
                                        binary_fullpath, 
                                        outputtype,
                                        remappingfile)

            summary[zipfile][artifact] = collecting_data_for_summary(duration)

            if postprocessed:
                log.info(f'Output written to: {postprocessed}')

        generate_summary_postprocessing(summary, zipfile, extract_path, '_summary.json')

        duration = get_duration_from_timespan(start)
        log.info(f'Post-processing of zip finished in: {duration}')

    duration = get_duration_from_timespan(start)
    log.info(f'Script finished in: {duration}')

if __name__ == '__main__':
    main()