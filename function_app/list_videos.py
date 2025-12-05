"""
List videos in Azure AI Video Indexer using managed identity (or local dev creds).

Environment variables required:
- AZURE_SUBSCRIPTION_ID
- AZURE_RESOURCE_GROUP
- AZURE_VIDEO_INDEXER_ACCOUNT_NAME
- AZURE_VIDEO_INDEXER_ACCOUNT_ID
- AZURE_VIDEO_INDEXER_LOCATION (default: westus3)
- MANAGED_IDENTITY_CLIENT_ID (when using a user-assigned identity)

Usage:
    python list_videos.py --top 25
    python list_videos.py --all
"""
import os
import sys
import logging
import argparse
from dataclasses import dataclass
from typing import Iterator, Dict, Any, Optional

import requests
from azure.identity import DefaultAzureCredential

VIDEO_INDEXER_API_URL = "https://api.videoindexer.ai"
ARM_ACCESS_TOKEN_URL = (
    "https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/"
    "{resource_group}/providers/Microsoft.VideoIndexer/accounts/{account_name}/"
    "generateAccessToken?api-version=2024-01-01"
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

@dataclass
class Config:
    account_id: str
    location: str
    managed_identity_client_id: Optional[str]
    subscription_id: str
    resource_group: str
    account_name: str


def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def load_config() -> Config:
    return Config(
        account_id=require_env("AZURE_VIDEO_INDEXER_ACCOUNT_ID"),
        location=os.getenv("AZURE_VIDEO_INDEXER_LOCATION", "westus3"),
        managed_identity_client_id=os.getenv("MANAGED_IDENTITY_CLIENT_ID"),
        subscription_id=require_env("AZURE_SUBSCRIPTION_ID"),
        resource_group=require_env("AZURE_RESOURCE_GROUP"),
        account_name=require_env("AZURE_VIDEO_INDEXER_ACCOUNT_NAME"),
    )


def get_video_indexer_access_token(cfg: Config) -> str:
    cred_kwargs = {}
    if cfg.managed_identity_client_id:
        cred_kwargs["managed_identity_client_id"] = cfg.managed_identity_client_id
    credential = DefaultAzureCredential(**cred_kwargs)

    arm_token = credential.get_token("https://management.azure.com/.default").token
    headers = {"Authorization": f"Bearer {arm_token}", "Content-Type": "application/json"}
    body = {"permissionType": "Contributor", "scope": "Account"}

    url = ARM_ACCESS_TOKEN_URL.format(
        subscription_id=cfg.subscription_id,
        resource_group=cfg.resource_group,
        account_name=cfg.account_name,
    )

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()["accessToken"]


def fetch_videos(cfg: Config, access_token: str, top: int = 25, skip: int = 0) -> Dict[str, Any]:
    url = f"{VIDEO_INDEXER_API_URL}/{cfg.location}/Accounts/{cfg.account_id}/Videos"
    params = {"accessToken": access_token, "pageSize": top, "skip": skip}
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code >= 300:
        logger.error("Response %s: %s", resp.status_code, resp.text)
    resp.raise_for_status()
    return resp.json()


def iter_all_videos(cfg: Config, access_token: str, page_size: int = 50) -> Iterator[Dict[str, Any]]:
    skip = 0
    while True:
        data = fetch_videos(cfg, access_token, top=page_size, skip=skip)
        values = data.get("values") or data.get("results") or data
        if not values:
            break
        for item in values:
            yield item
        if len(values) < page_size:
            break
        skip += page_size


def summarize(video: Dict[str, Any]) -> str:
    return (
        f"{video.get('id')} | {video.get('name')} | state={video.get('state')} | "
        f"privacy={video.get('privacyMode') or video.get('privacy')} | "
        f"created={video.get('createdTime')}"
    )


def main():
    parser = argparse.ArgumentParser(description="List videos in Azure AI Video Indexer")
    parser.add_argument("--top", type=int, default=25, help="Number of videos to list (default 25)")
    parser.add_argument("--skip", type=int, default=0, help="Skip N videos (default 0)")
    parser.add_argument("--all", action="store_true", help="List all videos (pages of 50)")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    token = get_video_indexer_access_token(cfg)

    if args.all:
        for vid in iter_all_videos(cfg, token, page_size=50):
            print(summarize(vid))
    else:
        data = fetch_videos(cfg, token, top=args.top, skip=args.skip)
        values = data.get("values") or data.get("results") or data
        if isinstance(values, dict) and "results" in values:
            values = values["results"]
        if isinstance(values, dict):
            # fallback: single object
            values = [values]
        for vid in values:
            print(summarize(vid))


if __name__ == "__main__":
    main()
