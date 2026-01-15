from core.utils.config import load_config
from core.utils.status import Status, update_status_in_log
from core.manager.queue import decode_message
from datetime import datetime
import logging as log
import json

log = log.getLogger(__name__)

Config = load_config()

def should_send_to_queue(managers, zipfile):

    if Config.blob_logtable_enabled:

        status = managers.table.get_status_zipfile(zipfile)

        if status in [Status.FAILED, Status.NEW, Status.UNQUEUED]:
            log.info(f'Sending to queue. Status is: {status}')
            return True
        else:
            log.info('Is processed or processing.')
        return False
    
def send_to_queue(managers, source_name, zipfile, sessionid):

    start = datetime.now()

    send_to_queue = managers.queue.send_message(zipfile, source_name)

    update_status_in_log(managers, Status.QUEUED, zipfile, source_name, sessionid, start)

    return send_to_queue

def get_message_in_queue(message):
    
    try:
        decoded_message = json.loads(decode_message(message))

        triagepackage = decoded_message.get('triagepackage')
        source_name = decoded_message.get('source_name')

        return source_name, triagepackage
    
    except Exception as e:
        log.error(f'Could not load message from queue: {e}')
        return None, None
    
def update_status_unqueued(managers, source_name, zipfile, sessionid):

    start = datetime.now()

    message_not_yet_processed = is_message_not_yet_processing(managers, zipfile)

    if message_not_yet_processed:
        update_status_in_log(managers, Status.UNQUEUED, zipfile, source_name, sessionid, start)

def is_message_not_yet_processing(managers, zipfile) -> bool:
    '''Returns True if the message is currently being processed.'''

    status_all = managers.table.get_status_zipfile(zipfile)
    
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