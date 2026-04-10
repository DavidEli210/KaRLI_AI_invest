import json
import logging
import os
from typing import Optional

import boto3
import requests
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# --- Config (set these as Lambda environment variables) ---
USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
BACKEND_BASE_URL = os.environ["BACKEND_BASE_URL"].rstrip("/")  # e.g. http://my-ecs-service/
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "60"))             # Cognito max is 60
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10")) # seconds

cognito = boto3.client("cognito-idp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def paginate_users(user_pool_id: str, page_size: int):
    """
    Generator that yields one user dict at a time, fetching pages lazily.
    Uses PaginationToken to avoid loading all users into memory at once.
    """
    kwargs = {
        "UserPoolId": user_pool_id,
        "Limit": page_size,
        # Only pull the fields we actually need
        "AttributesToGet": ["sub", "custom:alpaca_key"],
    }

    while True:
        try:
            response = cognito.list_users(**kwargs)
        except ClientError as e:
            logger.error("Cognito list_users failed: %s", e)
            raise

        for user in response.get("Users", []):
            yield user

        pagination_token: Optional[str] = response.get("PaginationToken")
        if not pagination_token:
            break
        kwargs["PaginationToken"] = pagination_token


def get_attr(user: dict, attr_name: str) -> Optional[str]:
    """Extract a single attribute value from a Cognito user object."""
    for attr in user.get("Attributes", []):
        if attr["Name"] == attr_name:
            return attr["Value"]
    return None


def call_trade_endpoint(user_id: str) -> bool:
    """
    POST to /trade/<user_id> on the ECS backend.
    Returns True on success, False on any error.
    """
    url = f"{BACKEND_BASE_URL}/trade/{user_id}"
    try:
        resp = requests.post(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        logger.info("POST %s → %s", url, resp.status_code)
        return True
    except requests.exceptions.Timeout:
        logger.error("Timeout calling %s", url)
    except requests.exceptions.HTTPError as e:
        logger.error("HTTP error calling %s: %s", url, e)
    except requests.exceptions.RequestException as e:
        logger.error("Request error calling %s: %s", url, e)
    return False


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    seen_alpaca_keys: set[str] = set()
    total = processed = skipped_no_key = skipped_duplicate = failed = 0

    for user in paginate_users(USER_POOL_ID, PAGE_SIZE):
        total += 1

        alpaca_key = get_attr(user, "custom:alpaca_key")
        user_id = get_attr(user, "sub")  # 'sub' is Cognito's stable unique ID

        # Skip users with no alpaca key configured
        if not alpaca_key:
            logger.warning("User %s has no custom:alpaca_key — skipping.", user_id)
            skipped_no_key += 1
            continue

        # Deduplicate by alpaca key
        if alpaca_key in seen_alpaca_keys:
            logger.warning(
                "Duplicate custom:alpaca_key '%s' for user %s — skipping.",
                alpaca_key,
                user_id,
            )
            skipped_duplicate += 1
            continue

        seen_alpaca_keys.add(alpaca_key)

        success = call_trade_endpoint(user_id)
        if success:
            processed += 1
        else:
            failed += 1

    summary = {
        "total_users": total,
        "processed": processed,
        "skipped_no_alpaca_key": skipped_no_key,
        "skipped_duplicate_alpaca_key": skipped_duplicate,
        "failed": failed,
    }
    logger.info("Run complete: %s", json.dumps(summary))
    return {"statusCode": 200, "body": json.dumps(summary)}