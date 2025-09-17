from ..utils.config import load_config
from datetime import datetime
from typing import Optional
import uuid

Config = load_config()

def run_azurefunction_watcher(source_name: str, mode: str):
    None


def run_azurefunction_processor(source_name: str, mode: str, messagequeue: Optional[object] = None):
    None


def run_localdevice(source_name: str, mode: str):

    start = datetime.now()
    sessionid = str(uuid.uuid4())
    managers = init(source_name, sessionid)



def run_start(source_name: str, mode: str, messagequeue: Optional[object] = None):


    if mode == 'azurefunction-processor' or mode == 'manual-poll':

        if mode == 'manual-poll':

            messagequeue = managers.queue.receive_messages()
            
        for message in messagequeue:
            
            source_name, zipfile = get_message_in_queue(message)

            run_zip_processor(managers, source_name, zipfile, sessionid, start)

        return

    if mode == 'azurefunction-watcher' or mode == 'local':

        zipfiles = list_zipfiles(managers, source_name)

        for zipfile in zipfiles:

            tobeprocessed = managers.table.determine_if_need_for_processing(zipfile, source_name, sessionid, Status, Config)

            if tobeprocessed:

                if mode == 'azurefunction-watcher':
                    send_to_queue(managers, source_name, zipfile, sessionid, start)

                if mode == 'local':
                    run_zip_processor(managers, source_name, zipfile, sessionid, start)

            else:
                continue

        return