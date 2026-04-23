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

    send_to_queue = managers.queue.send_message(zipfile, source_name)

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

def decode_message(message) -> dict:
    '''
    Decode a queue message from either an Azure Function trigger
    or the Azure Storage Queue SDK.

    Args:
        message: QueueMessage from azure.functions or azure.storage.queue.

    Returns:
        dict: Decoded message content.
    '''
    if hasattr(message, 'get_body'):
        # func.QueueMessage (from Azure Function trigger)
        content = message.get_body().decode('utf-8')
    else:
        # azure.storage.queue.QueueMessage (from SDK receive_messages)
        content = base64.b64decode(message.content).decode('utf-8')

    return json.loads(content)

def encode_messsage(zipfile: str, source_name: str):
    '''
    Encode a message for sending to Azure Queue Storage.

    The message content is serialized as JSON and Base64-encoded
    to comply with Azure Queue Storage requirements.

    Args:
        zipfile (str): Zipfile name to include in the message.
        source_name (str): Source name to include in the message.

    Returns:
        tuple[str, str]: Tuple containing:
            - Base64-encoded message string.
            - Original JSON message content.
    '''

    content = json.dumps({'triagepackage': zipfile, 'source_name' : source_name})
    message_bytes = content.encode('utf-8')
    base64_bytes = base64.b64encode(message_bytes)
    message_output = base64_bytes.decode('utf-8')

    return message_output, content