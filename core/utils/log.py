'''
Module for configuring application logging and runtime session identifiers.

Provides helper functions to:
    - Configure logging output to both console and file
    - Control log verbosity
    - Generate short unique session identifiers for a single run
'''

import logging as log
import os
import uuid
from datetime import datetime
from pathlib import Path
from core.utils.files import create_directory_if_not_exists

def set_loglevel(level):
    '''
    Translate a string log level into a logging module numeric level.

    Args:
        level (str): Log level as a string. Currently supports 'DEBUG'
            for debug-level logging. All other values default to INFO.

    Returns:
        int: Numeric logging level compatible with the logging module.
    '''

    if level == 'DEBUG':
        loglevel = 10
    else:
        loglevel = 20

    return loglevel

def generate_sessionid():
    '''
    Generate a short unique session identifier.

    Uses a UUID4 value truncated to 8 hexadecimal characters.

    Returns:
        str: Short unique session identifier.
    '''

    return str(uuid.uuid4().hex[:8])

def setup_logging(loglocation, loglevel):
    '''
    Configure application-wide logging.

    Sets up logging to write to both a timestamped log file and stdout,
    configures log formatting, and reduces verbosity for selected
    third-party libraries. Creates the log directory if it does not exist.

    Args:
        loglocation (str): Relative directory path where log files
            should be written.
        loglevel (str): Desired log level as a string (e.g. 'DEBUG',
            'INFO').

    Returns:
        str: Generated session identifier for the current runtime.
    '''

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