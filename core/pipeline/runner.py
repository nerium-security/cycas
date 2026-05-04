import logging
from core.utils.log import setup_logging
from core.pipeline.orchestrator import init, run_zip_processor
from core.utils.zip import list_zipfiles, list_zipfiles_with_sizes
from core.utils.status import Status, write_logentry_if_new, determine_if_needs_processing, add_summary_info_to_status, update_status_in_log
from core.utils.config import load_config
from core.utils.summary import define_results_dict
from core.utils.queue import update_status_unqueued, decode_message
from typing import Optional
from datetime import datetime

_MAX_PER_BATCH = 20

Config = load_config()

log = logging.getLogger(__name__)

def run_azurefunction_watcher() -> None:

    sessionid = setup_logging(Config.var_loglocation, Config.var_loglevel)

    Config.adx_cluster_enabled = False

    managers = init(sessionid, Config)
    zipfiles_with_sizes = list_zipfiles_with_sizes(managers, Config)

    threshold_bytes = Config.var_queue_batch_threshold_mb * 1024 * 1024
    large_pending: list[tuple[str, str, dict]] = []
    small_pending: list[tuple[str, str, dict]] = []

    for source_name, zipfiles in zipfiles_with_sizes.items():
        for zipfile, size in zipfiles:
            start = datetime.now()
            results = define_results_dict()
            results = add_summary_info_to_status(results, zipfile, sessionid, source_name, start)
            write_logentry_if_new(managers, Config, Status.NEW, results)

            if determine_if_needs_processing(managers, Config, zipfile):
                if size >= threshold_bytes:
                    large_pending.append((source_name, zipfile, results))
                else:
                    small_pending.append((source_name, zipfile, results))

    # Large files get one message each; small files are grouped up to _MAX_PER_BATCH per message
    batches: list[list[tuple[str, str, dict]]] = [[item] for item in large_pending]
    for i in range(0, len(small_pending), _MAX_PER_BATCH):
        batches.append(small_pending[i:i + _MAX_PER_BATCH])

    sent_count = 0
    for batch in batches:
        queue_items = [(zipfile, source_name) for source_name, zipfile, _ in batch]
        if managers.queue.send_message(queue_items):
            start = datetime.now()
            for source_name, zipfile, results in batch:
                update_status_in_log(managers, Config, Status.QUEUED, start, results)
            sent_count += len(batch)

    if sent_count == 0:
        log.info('No new triage packages found.')
    else:
        log.info(f'Queued {sent_count} triage package(s) in {len(batches)} message(s).')


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
            if mode == 'manual':
                managers.queue.delete_message(message)
            continue

        items = decode_message(message)  # always a list of {triagepackage, source_name}

        all_succeeded = True
        for item in items:
            zipfile = item['triagepackage']
            source_name = item['source_name']

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
                all_succeeded = False

        if mode == 'manual' and all_succeeded:
            managers.queue.delete_message(message)

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
