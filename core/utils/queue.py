'''
Helpers for queue interaction and status coordination.

Provides utility functions to:
    - Decide whether a zipfile should be sent to the processing queue
    - Send messages to an Azure Storage Queue
    - Decode and extract queue message contents
    - Update processing status for queued and unqueued items

These helpers integrate queue operations with Table Storage–based
status tracking to support idempotent processing.
'''

from core.utils.config import load_config
from core.utils.status import Status, update_status_in_log
from core.manager.queue import decode_message
from datetime import datetime
import logging as log
import json
import os

log = log.getLogger(__name__)

def should_send_to_queue(managers, Config, zipfile):
    '''
    Determine whether a zipfile should be sent to the processing queue.

    Checks the current processing status of the zipfile in Table Storage
    and allows queueing only if the status indicates a new or failed item.

    Args:
        managers: Container holding authenticated service managers.
        Config: variables defined in .env file
        zipfile (str): Zipfile basename used as the status lookup key.

    Returns:
        bool: True if the zipfile should be sent to the queue, otherwise False.
    '''

    if Config.blob_logtable_enabled:

        status = managers.table.get_status_zipfile(zipfile)

        if status in [Status.FAILED, Status.NEW, Status.UNQUEUED]:
            log.info(f'Sending to queue. Status is: {status}')
            return True
        else:
            log.info('Is processed or processing.')
        return False
    
def send_to_queue(managers, Config, source_name, zipfile, status_data):
    '''
    Send a zipfile reference to the processing queue and update its status.

    Sends a queue message containing the zipfile and source name, then
    records the QUEUED status in Table Storage.

    Args:
        managers: Container holding authenticated service managers.
        source_name (str): Source identifier associated with the zipfile.
        zipfile (str): Zipfile basename to send to the queue.
        status_data (dict): Metadata used to build the log entity.

    Returns:
        bool: Result of the queue send operation.
    '''

    start = datetime.now()

    send_to_queue = managers.queue.send_message(zipfile, source_name)

    update_status_in_log(managers, Config, Status.QUEUED, start, status_data)

    return send_to_queue

def get_message_in_queue(message):
    '''
    Decode and extract fields from a queue message.

    Decodes the Base64-encoded queue message payload, parses it as JSON,
    and extracts the source name and triage package name.

    Args:
        message: Queue message object as received from the queue client.

    Returns:
        tuple[str | None, str | None]: Tuple containing:
            - source_name
            - triagepackage
        Returns (None, None) if decoding or parsing fails.
    '''

    try:
        decoded_message = json.loads(decode_message(message))

        triagepackage = decoded_message.get('triagepackage')
        source_name = decoded_message.get('source_name')

        return source_name, triagepackage
    
    except Exception as e:
        log.error(f'Could not load message from queue: {e}')
        return None, None
    
def update_status_unqueued(managers, Config, zipfile, start, status_data):
    '''
    Update the status of a zipfile to UNQUEUED if it has not started processing.

    Checks whether the zipfile is still in a QUEUED state and, if so,
    updates its status to UNQUEUED in Table Storage.

    Args:
        managers: Container holding authenticated service managers.
        Config: variables defined in .env file
        zipfile (str): basename of zipfile
        status_data (dict): Metadata used to build the log entity.
    '''

    message_not_yet_processed = is_message_not_yet_processing(managers, zipfile)

    if message_not_yet_processed:
        update_status_in_log(managers, Config, Status.UNQUEUED, start, status_data)

def is_message_not_yet_processing(managers, zipfile) -> bool:
    '''
    Check whether a queued message has not yet started processing.

    Looks up the zipfile status in Table Storage and returns True only
    if the current status is QUEUED.

    Args:
        managers: Container holding authenticated service managers.
        zipfile (str): Zipfile basename used as the status lookup key.

    Returns:
        bool: True if the message is still queued and not yet processed.
        Returns None implicitly if no status entry exists.
    '''

    zip_basename = os.path.basename(zipfile)
    status_all = managers.table.get_status_zipfile(zip_basename)
    
    if status_all:
        status = status_all.get('Status')

        if status == Status.QUEUED:
            log.info('Message is not yet processed')
            return True
        
        elif status == Status.FAILED:
            log.info('Message was failed.')

        else:
            log.info('Message is already processed or processing')
            return False
    else:
        return