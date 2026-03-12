import logging
import requests
import json

logger = logging.getLogger(__name__)

async def fetch_from_new_api(phone_number: str, api_url_template: str):
    """
    Fetch data from the new API and return list of standardized records.
    api_url_template must contain {num} placeholder.
    """
    api_url = api_url_template.format(num=phone_number)
    logger.info(f"Calling API: {api_url}")
    try:
        response = requests.get(api_url, timeout=15)
        if response.status_code != 200:
            logger.warning(f"New API returned {response.status_code}")
            return None

        data = response.json()
        logger.info(f"New API response: {json.dumps(data, indent=2)[:500]}")

        # Extract the inner results (adjust this part if API response structure changes)
        if data.get("success") and data.get("type") == "success" and "data" in data:
            inner = data["data"]
            if inner.get("success") and inner.get("type") == "number_search" and "results" in inner:
                results = inner["results"]
                if results and isinstance(results, list):
                    records = []
                    for item in results:
                        record = {
                            "name": item.get("name", ""),
                            "father_name": item.get("fname", ""),
                            "address": item.get("address", ""),
                            "mobile": item.get("mobile", ""),
                            "alt_mobile": item.get("alt", ""),
                            "circle": item.get("circle", ""),
                            "id_number": item.get("id", ""),
                            "email": item.get("email", "")
                        }
                        records.append(record)
                    return records
                else:
                    logger.info("New API returned empty results list")
                    return None
        else:
            logger.info("New API returned failure status or no results")
            return None
    except Exception as e:
        logger.error(f"New API error: {e}")
        return None
