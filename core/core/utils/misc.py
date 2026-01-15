import requests
import logging as log
from datetime import datetime

log = log.getLogger(__name__)

def send_webhook(url: str, message: str) -> bool:
    ''' Send a JSON payload to a webhook URL via POST. '''

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
    Adds the unit "seconds" to be able to pretty print the results
    of artifact post-processing.
    '''

    return {k: f"{v:.2f} seconds" for k, v in summary.items()}

def calculate_total(summary):
    ''' 
    Sums the total of post-processing time and returns it with two
    decimals after the comma
    '''

    return round(sum(v for v in summary.values() if v is not None), 2)

def get_duration_from_timespan(start):
    ''' 
    Used to calculate the duration to output it in a human-friendly manner.
    
    Args:
        start = datetime.now()
    '''

    end = datetime.now()
    duration_seconds = (end - start).total_seconds()
    minutes, seconds = divmod(duration_seconds, 60)

    return f'{int(minutes)}m {int(seconds)}s'