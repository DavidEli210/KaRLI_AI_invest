import os
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError


COGNITO_REGION = os.getenv("COGNITO_REGION", "")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")

_cognito_client = boto3.client("cognito-idp", region_name=COGNITO_REGION or None)


def _require_pool_config() -> None:
    if not COGNITO_USER_POOL_ID:
        raise RuntimeError("Missing COGNITO_USER_POOL_ID environment variable.")


def _attributes_to_map(attributes: List[Dict[str, str]]) -> Dict[str, str]:
    return {attr["Name"]: attr["Value"] for attr in attributes}


def _get_cognito_username_by_sub(sub: str) -> Optional[str]:
    response = _cognito_client.list_users(
        UserPoolId=COGNITO_USER_POOL_ID,
        Filter=f'sub = "{sub}"',
        Limit=1,
    )
    users = response.get("Users", [])
    if not users:
        return None
    return users[0].get("Username")


def get_user_alpaca_credentials_by_sub(sub: str) -> Optional[Dict[str, str]]:
    _require_pool_config()
    if not sub:
        return None

    cognito_username = _get_cognito_username_by_sub(sub)
    if not cognito_username:
        return None

    try:
        response = _cognito_client.admin_get_user(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=cognito_username,
        )
    except ClientError:
        return None

    attrs = _attributes_to_map(response.get("UserAttributes", []))
    api_key = attrs.get("custom:alpaca_key")
    api_secret = attrs.get("custom:alpaca_secret")

    if not api_key or not api_secret:
        return None

    return {
        "api_key": api_key,
        "api_secret": api_secret,
    }


def get_all_users_with_credentials() -> List[Dict[str, str]]:
    _require_pool_config()
    users: List[Dict[str, str]] = []
    pagination_token: Optional[str] = None

    while True:
        request_params = {"UserPoolId": COGNITO_USER_POOL_ID}
        if pagination_token:
            request_params["PaginationToken"] = pagination_token

        response = _cognito_client.list_users(**request_params)
        for user in response.get("Users", []):
            attrs = _attributes_to_map(user.get("Attributes", []))
            api_key = attrs.get("custom:alpaca_key")
            api_secret = attrs.get("custom:alpaca_secret")
            if api_key and api_secret:
                users.append(
                    {
                        "username": user.get("Username", ""),
                        "api_key": api_key,
                        "api_secret": api_secret,
                    }
                )

        pagination_token = response.get("PaginationToken")
        if not pagination_token:
            break

    return users
