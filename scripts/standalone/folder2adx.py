import os
import sys
import argparse
import fileinput
import logging as log
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

from core.manager.adx import AdxManager
from core.utils.log import setup_logging
from core.utils.zip import load_ignore_list, is_ignored

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent

load_dotenv(PROJECT_ROOT / '.env')

script_path = sys.argv[0]
scriptname = os.path.basename(script_path)

parser = argparse.ArgumentParser(
    description='Upload a folder of JSONL files into Azure Data Explorer (ADX). Only .jsonl files are supported.',
    epilog=f'Example usage: python3 {scriptname} -i /path/to/folder'
)
parser.add_argument('-i', '--input', required=True,
                    help='Path to the folder containing JSONL files to upload.')
parser.add_argument('-r', '--recursive', action='store_true',
                    help='Recurse into subdirectories.')
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
parser.add_argument('--ignorelist',
                    default=str(PROJECT_ROOT / os.environ.get('VAR_LOCATION_IGNORELIST', '.ignorelist.json')),
                    help='Path to ignore list JSON file (default: .ignorelist.json in project root).')
parser.add_argument('-v', '--verbose', action='store_true',
                    help='Enable verbose (DEBUG) logging.')

args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])

log = log.getLogger(__name__)


def inject_sourcefilename(filepath: str) -> None:
    replacement = f',"Sourcefilename":"{filepath}"}}'
    for line in fileinput.input(filepath, inplace=True):
        line = line.rstrip('\n')
        if line.endswith('}'):
            line = line[:-1] + replacement
        print(line)


def find_files(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        return sorted(folder.rglob('*.jsonl'))
    return sorted(folder.glob('*.jsonl'))


def main():
    logpath = PROJECT_ROOT / 'scripts' / 'logs'
    loglevel = 'DEBUG' if args.verbose else 'INFO'
    setup_logging(str(logpath), loglevel)

    log.info('Script started.')

    folder = Path(args.input)
    if not folder.is_dir():
        log.error(f'Input path is not a directory: {folder}')
        sys.exit(1)

    files = find_files(folder, args.recursive)
    if not files:
        log.info(f'No JSONL files found in {folder}.')
        sys.exit(0)

    ignorelist = load_ignore_list(args.ignorelist)
    files = [
        f for f in files
        if not is_ignored(
            SimpleNamespace(filename=str(f), file_size=f.stat().st_size),
            ignorelist
        ).get('ignored_upload')
    ]

    log.info(f'Found {len(files)} file(s) to upload (after ignore list filtering).')

    missing = [
        (args.adx_cluster_uri,           'ADX_CLUSTER_URI / --adx-cluster-uri'),
        (args.adx_cluster_ingestion_uri,  'ADX_CLUSTER_INGESTION_URI / --adx-cluster-ingestion-uri'),
        (args.adx_database,               'ADX_DATABASE_NAME / --adx-database'),
    ]
    for value, label in missing:
        if not value:
            log.error(f'Missing required setting: {label}')
            sys.exit(1)

    credential = DefaultAzureCredential()
    adx = AdxManager(
        credential,
        args.adx_cluster_uri,
        args.adx_cluster_ingestion_uri,
        args.adx_database,
    )
    adx.authenticate(verify_enabled=False)

    config = SimpleNamespace(
        var_sample_size=args.sample_size,
        adx_table_prefix=args.table_prefix,
    )

    succeeded = 0
    failed = 0

    for file in files:
        filepath = str(file)
        log.info(f'Processing: {filepath}')

        inject_sourcefilename(filepath)

        tablename = adx.create_new_table_if_required(config, filepath, forcetablename=None)
        result = adx.launch_upload_file(tablename, filepath)

        if result.get('upload_initiated'):
            succeeded += 1
        else:
            log.error(
                f'Upload failed for {file.name}: {result.get("upload_error", "unknown error")}'
            )
            failed += 1

    log.info(f'Done. {succeeded} upload(s) initiated, {failed} failed.')


if __name__ == '__main__':
    main()
