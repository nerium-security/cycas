'''
Various helpers for notifications and runtime duration formatting.

Provides functions to:
    - Send simple JSON payloads to webhook endpoints
    - Format timing values for human-readable output
    - Calculate aggregate and formatted durations
'''

import requests
import logging as log
from datetime import datetime

log = log.getLogger(__name__)

def send_webhook(url: str, message: str) -> bool:
    '''
    Send a JSON-formatted message to a webhook endpoint via HTTP POST.

    Args:
        url (str): Webhook endpoint URL.
        message (str): Message content to send.

    Returns:
        bool: True if the webhook request succeeds, otherwise False.

    Notes:
        - Uses a 10-second request timeout.
        - HTTP and network errors are logged and not raised.
    '''

    payload = {"text": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        log.info(f"Webhook sent successfully to {url}")
        return True
    except requests.exceptions.Timeout:
        log.error(f"Request to {url} timed out.")
    except requests.exceptions.HTTPError as err:
        log.error(f"HTTP error: {err} - Response: {getattr(err.response, 'text', '')}")
    except requests.exceptions.RequestException as err:
        log.error(f"Request failed: {err}")
    except Exception as err:
        log.exception(f"Unexpected error: {err}")

    return False

def adding_seconds(summary):
    '''
    Append a human-readable "seconds" unit to timing values.

    Converts numeric values in the input mapping into formatted strings
    with two decimal places and a trailing "seconds" label.

    Args:
        summary (dict[str, float]): Mapping of labels to duration values
            in seconds.

    Returns:
        dict[str, str]: Mapping with formatted duration strings.
    '''

    return {k: f"{v:.2f} seconds" for k, v in summary.items()}

def calculate_total(summary):
    '''
    Calculate the total duration from a collection of timing values.

    Sums all non-None values in the input mapping and rounds the result
    to two decimal places.

    Args:
        summary (dict[str, float | None]): Mapping of labels to duration
            values in seconds.

    Returns:
        float: Total duration in seconds, rounded to two decimals.
    '''

    return round(sum(v for v in summary.values() if v is not None), 2)

def get_duration_from_timespan(start):
    '''
    Calculate a human-readable duration from a start time.

    Computes the elapsed time between the provided start time and the
    current time and formats it as minutes and seconds.

    Args:
        start (datetime): Start timestamp.

    Returns:
        str: Duration formatted as '<minutes>m <seconds>s'.
    '''

    end = datetime.now()
    duration_seconds = (end - start).total_seconds()
    minutes, seconds = divmod(duration_seconds, 60)

    return f'{int(minutes)}m {int(seconds)}s'

def should_download(source_name):
    '''
    Determine whether a zipfile should be downloaded or not.

    Args:
        source_name (str): Data source identifier.

    Returns:
        bool: False for 'localfolder' (already local), True for all other sources.
    '''

    if source_name == 'localfolder':
        return False
    else:
        return True