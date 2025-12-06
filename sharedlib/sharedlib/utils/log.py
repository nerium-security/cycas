import logging as log
import os
import uuid
from datetime import datetime
from pathlib import Path
from sharedlib.utils.files import create_directory_if_not_exists

def set_loglevel(level):
    if level == 'DEBUG':
        loglevel = 10
    else:
        loglevel = 20

    return loglevel

def generate_sessionid():
    return str(uuid.uuid4().hex[:8])

def setup_logging(loglocation, loglevel):

    create_directory_if_not_exists(loglocation)

    sessionid = generate_sessionid()
    root_folder = os.path.abspath(os.path.join(os.path.dirname(__name__), '..'))
    log_filename = f'{datetime.now():%Y-%m-%d_%H%M%S}' + '_log.txt'
    log_file_path = os.path.join(root_folder, loglocation, log_filename)

    LOGLEVEL = set_loglevel(loglevel)

    log.basicConfig(
        level=LOGLEVEL,
        format='%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %I:%M:%S',
        handlers=[
            log.FileHandler(Path(log_file_path), encoding="utf-8"),
            log.StreamHandler()
        ]
    )

    for logger_name in [
        'azure.core.pipeline.policies.http_logging_policy',
        'azure.identity',
        'paramiko',
    ]:
        log.getLogger(logger_name).setLevel(log.WARNING)

    return sessionid