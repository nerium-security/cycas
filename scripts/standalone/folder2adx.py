import os
import sys
import argparse
import logging as log
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

from core.manager.adx import AdxManager
from core.utils.log import setup_logging

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent

load_dotenv(PROJECT_ROOT / '.env')

script_path = sys.argv[0]
scriptname = os.path.basename(script_path)

parser = argparse.ArgumentParser(
    description='Upload a folder of JSONL files directly into Azure Data Explorer (ADX).',
    epilog=f'Example usage: python3 {scriptname} -i /path/to/folder'
)
parser.add_argument('-i', '--input', required=True,
                    help='Path to the folder containing files to upload.')
parser.add_argument('-p', '--pattern', default='*.jsonl',
                    help='Glob pattern for files to upload (default: *.jsonl).')
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
parser.add_argument('-v', '--verbose', action='store_true',
                    help='Enable verbose (DEBUG) logging.')

args = parser.parse_args(args=None if sys.argv[1:] else ['--help'])

log = log.getLogger(__name__)


def find_files(folder: Path, pattern: str, recursive: bool) -> list[Path]:
    if recursive:
        return sorted(folder.rglob(pattern))
    return sorted(folder.glob(pattern))


def main():
    logpath = PROJECT_ROOT / 'scripts' / 'logs'
    loglevel = 'DEBUG' if args.verbose else 'INFO'
    setup_logging(str(logpath), loglevel)

    log.info('Script started.')

    folder = Path(args.input)
    if not folder.is_dir():
        log.error(f'Input path is not a directory: {folder}')
        sys.exit(1)

    files = find_files(folder, args.pattern, args.recursive)
    if not files:
        log.info(f'No files matching "{args.pattern}" found in {folder}.')
        sys.exit(0)

    log.info(f'Found {len(files)} file(s) to upload.')

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
