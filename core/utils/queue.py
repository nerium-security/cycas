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

from core.utils.status import Status, update_status_in_log
from datetime import datetime
import base64
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

    send_to_queue = managers.queue.send_message([(zipfile, source_name)])

    update_status_in_log(managers, Config, Status.QUEUED, start, status_data)

    return send_to_queue
    
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

def decode_message(message) -> list[dict]:
    '''
    Decode a queue message into a list of triage package items.

    Handles both the current batch format and the legacy single-item format
    so messages already in the queue are processed correctly.

    Args:
        message: QueueMessage from azure.functions or azure.storage.queue.

    Returns:
        list[dict]: Each dict contains 'triagepackage' and 'source_name'.
    '''
    if hasattr(message, 'get_body'):
        content = message.get_body().decode('utf-8')
    else:
        content = base64.b64decode(message.content).decode('utf-8')

    data = json.loads(content)

    if 'triagepackages' in data:
        return data['triagepackages']

    # Legacy single-item format
    return [data]


def encode_message(items: list[tuple[str, str]]) -> tuple[str, str]:
    '''
    Encode one or more (zipfile, source_name) pairs for Azure Queue Storage.

    Args:
        items: List of (zipfile, source_name) tuples to bundle in one message.

    Returns:
        tuple[str, str]: (Base64-encoded message, original JSON string).
    '''
    payload = [{'triagepackage': zf, 'source_name': src} for zf, src in items]
    content = json.dumps({'triagepackages': payload})
    message_output = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    return message_output, content