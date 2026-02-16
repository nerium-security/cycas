import logging
from core.utils.log import setup_logging
from core.pipeline.orchestrator import (init, 
                                run_zip_processor, 
                                write_logentry_if_new, 
                                determine_if_needs_processing)
from core.utils.zip import list_zipfiles
from core.utils.status import Status
from core.utils.config import load_config
from core.utils.queue import send_to_queue, get_message_in_queue, update_status_unqueued
from typing import Optional

Config = load_config()

log = logging.getLogger(__name__)

def run_azurefunction_watcher(triagepackage_source: str) -> None:

    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)
    managers = init(triagepackage_source, sessionid)
    zipfiles = list_zipfiles(managers, triagepackage_source, Config)

    results = []
    for zipfile in zipfiles:

        write_logentry_if_new(managers, triagepackage_source, zipfile, sessionid, Status.NEW)

        needs_processing = determine_if_needs_processing(managers, zipfile)

        if needs_processing:
            results.append(send_to_queue(managers, triagepackage_source, zipfile, sessionid))

    if results:
        log.info(f'Sent {len(results)} zip file(s) to queue.')
    else:
        log.info('No new zip files. All are already sent to queue.')

def run_azurefunction_processor(triagepackage_source: str, mode: str, messagequeue: Optional[object] = None) -> None:
    
    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)
    managers = init(triagepackage_source, sessionid)

    if mode == 'manual':
        messagequeue = managers.queue.receive_messages()

    if not messagequeue:
        return

    for message in messagequeue:

        source_name, zipfile = get_message_in_queue(message)

        update_status_unqueued(managers, source_name, zipfile, sessionid)

        run_zip_processor(managers, source_name, zipfile, sessionid, message)

        #process_from_queue = should_process_from_queue()

        #update_table()

        #if process_from_queue:
        #    start = datetime.now()
        #    run_zip_processor(managers, source_name, zipfile, sessionid, start)


def run_localdevice(triagepackage_source: str) -> None:

    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)
    managers = init(triagepackage_source, sessionid)
    zipfiles = list_zipfiles(managers, triagepackage_source, Config)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    futures = []

    concurrency = Config.var_localdevice_concurrency
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                run_zip_processor,
                managers,
                triagepackage_source,
                zipfile,
                sessionid,
                None,
            )
            for zipfile in zipfiles
        ]

        for fut in as_completed(futures):
            fut.result()
            #run_zip_processor(managers, triagepackage_source, zipfile, sessionid)
