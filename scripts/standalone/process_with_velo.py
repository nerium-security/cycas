import os
import sys
import argparse
import logging as log
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

from core.manager.adx import AdxManager
from core.utils.log import setup_logging
from core.utils.status import (add_summary_info_to_status,
                                    add_hostname_to_status)

from core.utils.postprocess import (download_velociraptor,
                                         build_remap,
                                         find_hostname,
                                         load_artifacts,
                                         select_artifacts,
                                         postprocess,
                                         write_command_to_logfile)

from core.utils.summary import (define_results_dict,
                                     pretty_print_summary_per_zip,
                                     get_duration_from_timespan,
                                     build_all_zip_summary,
                                     pretty_print_master_table,
                                     summary_per_zip_to_file,
                                     merge_master_table_with_file)

from core.utils.zip import (extract_encrypted_and_non_encrypted_zipfiles,
                                 get_password,
                                 load_from_env_variable,
                                 find_zip_files,
                                 list_files_in_zip,
                                 extract_single_file)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent

load_dotenv(PROJECT_ROOT / '.env')

script_path = sys.argv[0]
scriptname = os.path.basename(script_path)
parser = argparse.ArgumentParser(
description = 'This script post-processes zip files containing raw artifact files collected with velociraptor.',
epilog=f'Example usage: python3 {scriptname} -i *.zip')
parser.add_argument('-o', '--outputtype', default='jsonl', choices=['csv', 'jsonl', 'json'])
parser.add_argument('-c', '--custom_definitions', default=PROJECT_ROOT / 'velociraptor/definitions/')
parser.add_argument('-b', '--binary', default=PROJECT_ROOT / 'velociraptor/velociraptor')
parser.add_argument('-u', '--url', default='https://github.com/Velocidex/velociraptor/releases/download/v0.74/velociraptor-v0.74.5-linux-amd64')
parser.add_argument('-i', '--input', nargs='+',  help='Path(s) to ZIP file(s). Supports wildcards (e.g. *.zip or **/*.zip)')
parser.add_argument('-f', '--outputfolder', help='If not set it will output next to the zip file.')
parser.add_argument('-a', '--artifacts', default=PROJECT_ROOT / 'velociraptor/artifacts/velociraptor_artifacts.json')
parser.add_argument('-v', '--verbose', help='Enables verbose logging', action='store_true')
parser.add_argument('-m', '--master_summary', default='master_summary.csv', help='Outputs the master summary of all processed zips to a file')
parser.add_argument('-s', '--artifact_summary', default='_summary.txt', help='Outputs detailed summary per single zip to a file.')
parser.add_argument('-l', '--log_commands', default='_commandhistory.txt', help='Outputs the executed commands to a file.')
parser.add_argument('-d', '--duration_in_seconds', default=400, type=int, help='Sets maximum duration of postprocessing an artifact with Velociraptor.')
parser.add_argument('--ingest', action='store_true', help='Ingest JSONL output directly into Azure Data Explorer after post-processing.')
parser.add_argument('--adx-cluster-uri',
                    default=os.environ.get('ADX_CLUSTER_URI', ''),
                    help='ADX cluster query URI (overrides .env ADX_CLUSTER_URI).')
parser.add_argument('--adx-cluster-ingestion-uri',
                    default=os.environ.get('ADX_CLUSTER_INGESTION_URI', ''),
                    help='ADX cluster ingestion URI (overrides .env ADX_CLUSTER_INGESTION_URI).')
parser.add_argument('--adx-database',
                    default=os.environ.get('ADX_DATABASE_NAME', ''),
                    help='ADX database name (overrides .env ADX_DATABASE_NAME).')
parser.add_argument('--table-prefix',
                    default=os.environ.get('ADX_TABLE_PREFIX', ''),
                    help='Optional prefix for ADX table names (overrides .env ADX_TABLE_PREFIX).')
parser.add_argument('--sample-size', default=1000, type=int,
                    help='Rows sampled per file for ADX schema inference (default: 1000).')
args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])

binary = args.binary
url = args.url
inputpath = args.input
custom_definitions = args.custom_definitions
outputtype = args.outputtype
verbose = args.verbose
artifacts = args.artifacts
outputfolder = args.outputfolder
master_summary = args.master_summary
artifact_summary = args.artifact_summary
log_commands = args.log_commands
duration_in_seconds = args.duration_in_seconds
ingest = args.ingest

log = log.getLogger(__name__)

def main():

    scriptlocation = os.path.dirname(os.path.realpath(__file__))
    definitions = os.path.join(scriptlocation, custom_definitions)
    binary_fullpath = os.path.join(scriptlocation, binary)
    master_summary_fullpath = os.path.join(scriptlocation, master_summary)
    logpath = os.path.join(scriptlocation, '../logs')
    start = datetime.now()
    loglevel = 'DEBUG' if verbose else 'INFO'
    setup_logging(logpath, loglevel)

    log.info('Script started.')

    adx = None
    adx_config = None
    if ingest:
        if outputtype != 'jsonl':
            log.error('--ingest requires --outputtype jsonl.')
            sys.exit(1)
        missing = [
            (args.adx_cluster_uri,          'ADX_CLUSTER_URI / --adx-cluster-uri'),
            (args.adx_cluster_ingestion_uri, 'ADX_CLUSTER_INGESTION_URI / --adx-cluster-ingestion-uri'),
            (args.adx_database,             'ADX_DATABASE_NAME / --adx-database'),
        ]
        for value, label in missing:
            if not value:
                log.error(f'Missing required setting for --ingest: {label}')
                sys.exit(1)

        credential = DefaultAzureCredential()
        adx = AdxManager(credential, args.adx_cluster_uri, args.adx_cluster_ingestion_uri, args.adx_database)
        adx.authenticate(verify_enabled=False)
        adx_config = SimpleNamespace(
            var_sample_size=args.sample_size,
            adx_table_prefix=args.table_prefix,
        )

    zipfiles = find_zip_files(inputpath)
    if len(zipfiles) == 0:
        log.info('Found 0 zipfiles.')
        log.info('Exiting script')
        exit()

    download_velociraptor(binary_fullpath, url)

    passwords = load_from_env_variable()

    artifacts_fullpath = os.path.join(scriptlocation, artifacts)
    artifacts_json = load_artifacts(artifacts_fullpath)
    artifacts_selected = select_artifacts(artifacts_json)

    all_zip_summaries = []
    for zipfile in zipfiles:

        results = define_results_dict()

        zipfile = str(zipfile)

        results = add_summary_info_to_status(results, zipfile, None, None, start)

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

        zipfilecontent = list_files_in_zip(extracted_zip, zip_password)
        client_info_entry = next((f for f in zipfilecontent if f.filename == 'client_info.json'), None)
        if client_info_entry:
            extract_single_file(extracted_zip, client_info_entry, extract_path, zip_password)

        remappingfile = build_remap(extracted_zip,
                                    extract_path,
                                    binary_fullpath,
                                    definitions,
                                    extract_path)

        hostname = find_hostname(remappingfile,
                                 binary_fullpath,
                                 definitions)

        add_hostname_to_status(results, None, hostname)

        for artifact in artifacts_selected:

            result_postprocess = postprocess(artifact,
                                        zipfile,
                                        definitions,
                                        extract_path,
                                        binary_fullpath,
                                        outputtype,
                                        remappingfile,
                                        duration_in_seconds)

            write_command_to_logfile(result_postprocess, extract_path, log_commands)

            results['postprocessing'].append(result_postprocess)

            if adx and result_postprocess.get('success') and result_postprocess.get('size', 0) > 0:
                fullpath = result_postprocess.get('fullpath')
                if fullpath:
                    tablename, col_mappings = adx.create_new_table_if_required(adx_config, fullpath, forcetablename=None)
                    adx.launch_upload_file(tablename, fullpath, column_mappings=col_mappings)

        summary_per_zip = pretty_print_summary_per_zip(extract_path, results, mode='full')

        summary_per_zip_to_file(summary_per_zip, extract_path, artifact_summary)

        all_zip_summary = build_all_zip_summary(results)
        all_zip_summaries.append(all_zip_summary)

        duration = get_duration_from_timespan(start)
        log.info(f'Post-processing of zip finished in: {duration}')

    pretty_print_master_table(all_zip_summaries)
    merge_master_table_with_file(all_zip_summaries, master_summary_fullpath)

    duration = get_duration_from_timespan(start)
    log.info(f'Script finished in: {duration}')

if __name__ == '__main__':
    main()
