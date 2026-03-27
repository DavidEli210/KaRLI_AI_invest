import os
from pymongo import MongoClient
from Enums import rl_variables

def create_client():
    MONGO_URI = os.getenv("MONGO_URI")
    client = MongoClient(MONGO_URI)
    db = client["KaRLi"]
    users_collection = db["users"]
    stocksDB = client["stock_data_db"]
    norm_collection = stocksDB["daily_stock_data_normalized"]
    meta_collection = stocksDB["normalization_metadata"]

    return client, users_collection, stocksDB, norm_collection, meta_collection

def sign_up(username, password, age, broker_api_key, broker_api_secret):
    client, users_collection, _, _, _ = create_client()
    try:
        document = users_collection.find_one({"username": username})

        if document:
            return False
        users_collection.insert_one({
            "username": username,
            "password": password,
            "age": age,
            "brokerApiKey": broker_api_key,
            "brokerApiSecret": broker_api_secret
        })
        return True
    finally:
        client.close()


def sign_in(username, password):
    client, users_collection, _, _, _ = create_client()
    try:
        document = users_collection.find_one({"username": username, "password": password})
        return bool(document)
    finally:
        client.close()


def get_user_brokerApi_credentials(username):
    client, users_collection, _, _, _ = create_client()
    try:
        document = users_collection.find_one({"username": username})

        if document:
            return {
                "api_key": document.get("brokerApiKey"),
                "api_secret": document.get("brokerApiSecret")
            }
        return None
    finally:
        client.close()

def get_all_users_with_credentials():
    client, users_collection, _, _, _ = create_client()
    try:
        users = []

        for user in users_collection.find({}):
            username = user.get("username")
            api_key = user.get("brokerApiKey")
            api_secret = user.get("brokerApiSecret")

            users.append({
                "username": username,
                "api_key": api_key,
                "api_secret": api_secret,
            })

        return users
    finally:
        client.close()

def load_stats(ticker: str) -> dict:
    client, _, _, _, meta_collection = create_client()
    try:
        stats = {d["feature"]: (d["mean"], d["std"])
                 for d in meta_collection.find({"ticker": ticker})}
        missing = set(rl_variables.FEATURE_COLS) - set(stats)
        if missing:
            raise ValueError(f"metadata missing for {missing}")
        return stats
    finally:
        client.close()

def insert_daily_data(tickers_data):
    client, _, _, norm_collection, _ = create_client()
    try:
        records = tickers_data.to_dict(orient='records')

        if records:
            norm_collection.insert_many(records)
    finally:
        client.close()

def fetch_data_for_inference(ticker: str, window_size: int = 30):
    client, _, _, norm_collection, _ = create_client()
    try:
        return norm_collection.find({"ticker": ticker}).sort("date", -1).limit(window_size)
    finally:
        client.close()