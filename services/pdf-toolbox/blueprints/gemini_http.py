import time

import requests


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def post_generate_content(url, payload, *, timeout=120, max_attempts=4, logger=None):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            if response.status_code == 200:
                return response
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response

            last_error = f"Gemini API Error ({response.status_code}): {response.text}"
            if logger:
                logger.warning("Retryable Gemini API error on attempt %s/%s: %s", attempt, max_attempts, last_error)
        except requests.RequestException as exc:
            last_error = f"Gemini API request failed: {exc}"
            if logger:
                logger.warning("Retryable Gemini request exception on attempt %s/%s: %s", attempt, max_attempts, exc)

        if attempt < max_attempts:
            time.sleep(min(2 ** (attempt - 1), 8))

    raise RuntimeError(last_error or "Gemini API request failed")
