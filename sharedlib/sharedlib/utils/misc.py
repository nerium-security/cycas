import requests
import logging as log

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