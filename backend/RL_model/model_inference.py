# import os
# import logging
# import numpy as np
# import pandas as pd
# import boto3
# from Enums import rl_variables
# from mongo_utils import fetch_data_for_inference
# import datetime as dt
# from pathlib import Path
# from typing import List, Tuple
# from RL_model.model_manager import ModelManager

# logger = logging.getLogger(__name__)

# def _download_models_from_s3(model_dir: Path) -> None:
#     """
#     Populate `model_dir` from S3 so ModelManager can load local `*_best_model.zip` files.

#     Env vars:
#       - MODEL_S3_BUCKET (required for S3 mode)
#       - MODEL_S3_PREFIX (optional; defaults to "best models")
#       - MODEL_S3_FORCE_DOWNLOAD (optional; "1" to force re-sync even if cache exists)
#     """
#     bucket = os.environ.get("MODEL_S3_BUCKET", "").strip()
#     prefix = os.environ.get("MODEL_S3_PREFIX", "best models").strip().strip("/")
#     force_download = os.environ.get("MODEL_S3_FORCE_DOWNLOAD", "0").strip() == "1"

#     if not bucket:
#         raise RuntimeError("MODEL_S3_BUCKET env var is required to download models from S3.")

#     model_dir.mkdir(parents=True, exist_ok=True)

#     cached_models = list(model_dir.glob("*_best_model.zip"))
#     if cached_models and not force_download:
#         logger.info("Using cached models (%d) from %s.", len(cached_models), model_dir)
#         return

#     s3 = boto3.client("s3")
#     paginator = s3.get_paginator("list_objects_v2")

#     prefix_with_slash = f"{prefix}/" if prefix else ""
#     downloaded_any = False
#     for page in paginator.paginate(Bucket=bucket, Prefix=prefix_with_slash):
#         for obj in page.get("Contents", []) or []:
#             key = obj.get("Key")
#             if not key or not key.endswith("_best_model.zip"):
#                 continue

#             filename = Path(key).name
#             target_path = model_dir / filename

#             # Avoid re-downloading files if they already exist locally (unless forced).
#             if target_path.exists() and not force_download:
#                 continue

#             logger.info("Downloading s3://%s/%s -> %s", bucket, key, target_path)
#             s3.download_file(bucket, key, str(target_path))
#             downloaded_any = True

#     if not downloaded_any:
#         raise RuntimeError(
#             f"No models found in s3://{bucket}/{prefix_with_slash} matching '*_best_model.zip'. "
#             "Check MODEL_S3_PREFIX and that the files exist."
#         )

# # Lazy initialization — only download & load models when first needed
# script_dir = Path(__file__).parent
# model_dir = script_dir / "best_models"

# _model_manager = None

# def _get_model_manager():
#     global _model_manager
#     if _model_manager is None:
#         _download_models_from_s3(model_dir)
#         _model_manager = ModelManager(model_dir)
#         _model_manager.load_all_models()
#     return _model_manager

# # Build today’s observation window (29 hist + 1 today)
# def build_obs(ticker:str) -> np.ndarray:
#     hist = pd.DataFrame(list(
#         fetch_data_for_inference(ticker, 30)
#     ))[::-1]                             # oldest→newest
#     if len(hist) < rl_variables.WINDOW-1:
#         raise ValueError("Not enough history in DB")

#     hist_features = hist[rl_variables.FEATURE_COLS]
#     hist_features = hist_features.reset_index(drop=True)
#     obs = hist_features.values.astype(np.float32).flatten()
#     obs = np.concatenate([obs, [rl_variables.ACCOUNT_FLAG]], dtype=np.float32)
#     return obs.reshape(1, -1)


# def predict_stocks_actions(tickers: List[str]) -> List[Tuple[str, str]]:
#     tickers_actions = []

#     for ticker in tickers:
#         model = _get_model_manager().get_model(ticker)
#         if model is None:
#             print(f"[SKIP] No model found for {ticker}")
#             continue

#         obs = build_obs(ticker)
#         action, _ = model.predict(obs, deterministic=True)
#         action_int = int(action.item())
#         label = {0: "HOLD", 1: "BUY", 2: "SELL"}[action_int]
#         print(f"🗓 {dt.date.today()}   {ticker}:  {label}  (action={action_int})")
#         tickers_actions.append((ticker, label))

#     return tickers_actions
