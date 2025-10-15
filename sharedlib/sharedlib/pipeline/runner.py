import uuid
import logging as log
from ..pipeline.helpers import (init, 
                                list_zipfiles, 
                                run_zip_processor, 
                                send_to_queue, 
                                get_message_in_queue, 
                                write_logentry_if_new, 
                                update_status_unqueued, 
                                determine_if_needs_processing)
from ..utils.status import Status
from datetime import datetime
from typing import Optional

log = log.getLogger(__name__)

def run_azurefunction_watcher(triagepackage_source: str) -> None:

    sessionid = str(uuid.uuid4())
    managers = init(triagepackage_source, sessionid)
    zipfiles = list_zipfiles(managers, triagepackage_source)

    for zipfile in zipfiles:

        start = datetime.now()
        write_logentry_if_new(managers, triagepackage_source, zipfile, sessionid, Status.NEW)

        needs_processing = determine_if_needs_processing(managers, zipfile)

        if needs_processing:
            send_to_queue(managers, triagepackage_source, zipfile, sessionid, start)


        #send_to_queue = is_already_processing(managers, zipfile, triagepackage_source)

        #tobeprocessed = managers.table.determine_if_need_for_processing(zipfile, triagepackage_source, sessionid, Status.QUEUED, Config)
        #if send_to_queue:
        #    send_to_queue(managers, triagepackage_source, zipfile, sessionid, start)

def run_azurefunction_processor(triagepackage_source: str, mode: str, messagequeue: Optional[object] = None) -> None:
    
    sessionid = str(uuid.uuid4())
    managers = init(triagepackage_source, sessionid)

    if mode == 'manual':
        messagequeue = managers.queue.receive_messages()

    if not messagequeue:
        log.info('No messages in queue found.')
        return

    for message in messagequeue:
        source_name, zipfile = get_message_in_queue(message)

        start = datetime.now()

        update_status_unqueued(managers, source_name, zipfile, sessionid, start)
    
        run_zip_processor(managers, source_name, zipfile, sessionid, start)


        #process_from_queue = should_process_from_queue()

        #update_table()

        #if process_from_queue:
        #    start = datetime.now()
        #    run_zip_processor(managers, source_name, zipfile, sessionid, start)


def run_localdevice(triagepackage_source: str) -> None:

    sessionid = str(uuid.uuid4())
    managers = init(triagepackage_source, sessionid)
    zipfiles = list_zipfiles(managers, triagepackage_source)

    for zipfile in zipfiles:

        start = datetime.now()

        write_logentry_if_new(managers, triagepackage_source, zipfile, sessionid, Status.NEW)

        run_zip_processor(managers, triagepackage_source, zipfile, sessionid, start)
