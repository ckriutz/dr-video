#!/usr/bin/env python
"""
Get transcript for a Video Indexer video and print to console.

Env vars (same as your other scripts):
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZURE_VIDEO_INDEXER_ACCOUNT_NAME
  AZURE_VIDEO_INDEXER_ACCOUNT_ID
  AZURE_VIDEO_INDEXER_LOCATION (default: westus3)
  MANAGED_IDENTITY_CLIENT_ID (if using UAMI)
"""
import os, sys, logging, argparse, requests
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from azure.identity import DefaultAzureCredential

VIDEO_INDEXER_API_URL = "https://api.videoindexer.ai"
ARM_ACCESS_TOKEN_URL = (
    "https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/"
    "{resource_group}/providers/Microsoft.VideoIndexer/accounts/{account_name}/"
    "generateAccessToken?api-version=2024-01-01"
)

log = logging.getLogger("get_transcript")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

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

def get_vi_token(cfg: Config) -> str:
    cred_kwargs = {}
    if cfg.managed_identity_client_id:
        cred_kwargs["managed_identity_client_id"] = cfg.managed_identity_client_id
    cred = DefaultAzureCredential(**cred_kwargs)
    arm_token = cred.get_token("https://management.azure.com/.default").token
    resp = requests.post(
        ARM_ACCESS_TOKEN_URL.format(
            subscription_id=cfg.subscription_id,
            resource_group=cfg.resource_group,
            account_name=cfg.account_name,
        ),
        headers={"Authorization": f"Bearer {arm_token}", "Content-Type": "application/json"},
        json={"permissionType": "Contributor", "scope": "Account"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["accessToken"]

def get_video_index(cfg: Config, access_token: str, video_id: str) -> Dict[str, Any]:
    url = f"{VIDEO_INDEXER_API_URL}/{cfg.location}/Accounts/{cfg.account_id}/Videos/{video_id}/Index"
    resp = requests.get(url, params={"accessToken": access_token}, timeout=60)
    resp.raise_for_status()
    return resp.json()

def extract_transcript(idx: Dict[str, Any], include_timecodes: bool = False) -> List[str]:
    lines: List[str] = []
    buckets = []

    vids = idx.get("videos") or []
    if vids:
        buckets.append(vids[0].get("insights", {}).get("transcript", []))
    buckets.append(idx.get("insights", {}).get("transcript", []))

    for bucket in buckets:
        for entry in bucket or []:
            text = entry.get("text") or entry.get("displayText")
            if not text:
                continue
            if (instances := entry.get("instances")):
                start = instances[0].get("start") or instances[0].get("startTime")
                end = instances[0].get("end") or instances[0].get("endTime")
            else:
                tr = entry.get("timeRange") or {}
                start, end = tr.get("start"), tr.get("end")
            if include_timecodes:
                lines.append(f"[{start or ''}-{end or ''}] {text}".strip())
            else:
                lines.append(text)
    return lines

def main():
    parser = argparse.ArgumentParser(description="Print Video Indexer transcript")
    parser.add_argument("video_id", help="Video Indexer video ID")
    parser.add_argument("--timecodes", action="store_true", help="Include start/end timecodes")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except RuntimeError as e:
        log.error(str(e))
        sys.exit(1)

    token = get_vi_token(cfg)
    idx = get_video_index(cfg, token, args.video_id)
    transcript_lines = extract_transcript(idx, include_timecodes=args.timecodes)

    if not transcript_lines:
        log.info("No transcript lines found.")
        return

    for line in transcript_lines:
        print(line)

if __name__ == "__main__":
    main()