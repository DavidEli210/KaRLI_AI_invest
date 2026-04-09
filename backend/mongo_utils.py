import os
from pymongo import MongoClient
from Enums import rl_variables

MONGO_URI = os.getenv("MONGO_URI",
                      "mongodb+srv://adm:Aa123456@karli.mongocluster.cosmos.azure.com/?tls=true&authMechanism=SCRAM-SHA-256&retrywrites=false&maxIdleTimeMS=120000")
client = MongoClient(MONGO_URI)
db = client["KaRLi"]
stocksDB = client["stock_data_db"]
norm_collection = stocksDB["daily_stock_data_normalized"]
meta_collection = stocksDB["normalization_metadata"]

def load_stats(ticker: str) -> dict:
    stats = {d["feature"]: (d["mean"], d["std"])
             for d in meta_collection.find({"ticker": ticker})}
    missing = set(rl_variables.FEATURE_COLS) - set(stats)
    if missing:
        raise ValueError(f"metadata missing for {missing}")
    return stats

def insert_daily_data(tickers_data):
    records = tickers_data.to_dict(orient='records')

    if records:
        norm_collection.insert_many(records)

def fetch_data_for_inference(ticker: str, window_size: int = 30):
    return norm_collection.find({"ticker": ticker}).sort("date", -1).limit(window_size)