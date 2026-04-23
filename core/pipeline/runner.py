import logging
from core.utils.log import setup_logging
from core.pipeline.orchestrator import init, run_zip_processor
from core.utils.zip import list_zipfiles
from core.utils.status import Status, write_logentry_if_new, determine_if_needs_processing, add_summary_info_to_status
from core.utils.config import load_config
from core.utils.summary import define_results_dict
from core.utils.queue import send_to_queue, update_status_unqueued, decode_message, encode_messsage
from typing import Optional
from datetime import datetime

Config = load_config()

log = logging.getLogger(__name__)

def run_azurefunction_watcher() -> None:

    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)

    Config.adx_cluster_enabled = False
    
    managers = init(sessionid, Config)
    zipfiles = list_zipfiles(managers, Config)
    
    final = []

    for source_name, zipfiles in zipfiles.items():
        for zipfile in zipfiles:

            start = datetime.now()

            results = define_results_dict()
            results = add_summary_info_to_status(results, zipfile, sessionid, source_name, start)

            write_logentry_if_new(managers, Config, Status.NEW, results)

            needs_processing = determine_if_needs_processing(managers, Config, zipfile)

            if needs_processing:
                final.append(send_to_queue(managers, Config, source_name, zipfile, results))

    if not final:
        log.info('No new triage packages found.')
    else:
        log.info(f'Found {len(final)} triage packages.')


def run_azurefunction_processor(mode: str, messagequeue: Optional[object] = None) -> None:

    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)
    managers = init(sessionid, Config)

    if mode == 'manual':
        messagequeue = managers.queue.receive_messages()

    if not messagequeue:
        log.info('No new messages in queue.')
        return

    for message in messagequeue:

        if message.dequeue_count > Config.var_max_retry:
            
            managers.queue.delete_message(message)
            continue

        decoded_message = decode_message(message)
        
        zipfile = decoded_message.get('triagepackage')
        source_name = decoded_message.get('source_name')

        start = datetime.now()
        results = define_results_dict()
        results = add_summary_info_to_status(results, zipfile, sessionid, source_name, start)

        update_status_unqueued(managers, Config, zipfile, start, results)

        try:

            run_zip_processor(managers, source_name, zipfile, sessionid, Config)

        except Exception as e:
            log.error(
                f'Processing failed for {zipfile} '
                f'(attempt {message.dequeue_count}/{Config.var_max_retry}): {e}'
                )

def run_localdevice() -> None:

    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)
    managers = init(sessionid, Config)
    zipfiles = list_zipfiles(managers, Config)
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    futures = []

    concurrency = Config.var_localdevice_concurrency
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                run_zip_processor,
                managers,
                source_name,
                zipfile,
                sessionid,
                Config,
            )
            for source_name, zipfiles in zipfiles.items()
            for zipfile in zipfiles
        ]

        for fut in as_completed(futures):
            fut.result()

    log.info('Script finished')
