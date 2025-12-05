import os
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import azure.functions as func
import requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

app = func.FunctionApp()

# Configuration from environment variables
VIDEO_INDEXER_ACCOUNT_ID = os.environ.get("AZURE_VIDEO_INDEXER_ACCOUNT_ID")
VIDEO_INDEXER_LOCATION = os.environ.get("AZURE_VIDEO_INDEXER_LOCATION", "westus3")
MANAGED_IDENTITY_CLIENT_ID = os.environ.get("MANAGED_IDENTITY_CLIENT_ID")
STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME")
BLOB_SAS_VERSION = os.environ.get("AZURE_STORAGE_SAS_VERSION", "2022-11-02")

# Constants
CONTAINER_NAME = "dr-videos"
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".wmv", ".mkv", ".webm", ".flv"}

# Azure Video Indexer API endpoints
VIDEO_INDEXER_API_URL = "https://api.videoindexer.ai"
ARM_ACCESS_TOKEN_URL = (
    "https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/"
    "{resource_group}/providers/Microsoft.VideoIndexer/accounts/{account_name}/"
    "generateAccessToken?api-version=2024-01-01"
)

# Shared credential instance (supports Managed Identity in Azure, developer creds locally)
_credential = DefaultAzureCredential(managed_identity_client_id=MANAGED_IDENTITY_CLIENT_ID)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_video_indexer_access_token() -> str:
    """Get an access token for Azure Video Indexer using managed identity."""
    logger.info("Getting Video Indexer access token.")
    arm_token = _credential.get_token("https://management.azure.com/.default").token

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
    account_name = os.environ.get("AZURE_VIDEO_INDEXER_ACCOUNT_NAME")

    url = ARM_ACCESS_TOKEN_URL.format(
        subscription_id=subscription_id,
        resource_group=resource_group,
        account_name=account_name,
    )
    headers = {"Authorization": f"Bearer {arm_token}", "Content-Type": "application/json"}
    body = {"permissionType": "Contributor", "scope": "Account"}

    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()

    logger.info("🎉 Obtained Video Indexer access token successfully.")
    return response.json()["accessToken"]


def get_blob_sas_url(blob_name: str) -> str:
    """Generate a SAS URL for the blob to be used by Video Indexer."""
    logger.info(f"🔧 Generating SAS URL for blob: {blob_name}")

    account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=_credential)

    now = datetime.now(timezone.utc)
    skew = timedelta(minutes=5)
    user_delegation_key = blob_service_client.get_user_delegation_key(
        key_start_time=now - skew,
        key_expiry_time=now + timedelta(hours=2),
    )

    sas_token = generate_blob_sas(
        account_name=STORAGE_ACCOUNT_NAME,
        container_name=CONTAINER_NAME,
        blob_name=blob_name,
        user_delegation_key=user_delegation_key,
        permission=BlobSasPermissions(read=True),
        expiry=now + timedelta(hours=2),
        start=now - skew,
        version=BLOB_SAS_VERSION,
    )

    logger.info("🎉 Generated SAS URL for blob successfully.")
    return f"{account_url}/{CONTAINER_NAME}/{blob_name}?{sas_token}"


def submit_video_to_indexer(video_url: str, video_name: str) -> dict:
    """Submit a video to Azure AI Video Indexer for processing."""
    logger.info(f"🔧 Submitting video '{video_name}' to Azure AI Video Indexer.")

    access_token = get_video_indexer_access_token()
    upload_url = f"{VIDEO_INDEXER_API_URL}/{VIDEO_INDEXER_LOCATION}/Accounts/{VIDEO_INDEXER_ACCOUNT_ID}/Videos"

    # Decode so requests encodes exactly once
    video_url_param = unquote(video_url)
    params = {
        "accessToken": access_token,
        "name": video_name,
        "privacy": "Private",
        "language": "auto",
        "indexingPreset": "Default",
        "streamingPreset": "Default",
        "videoUrl": video_url_param,
    }
    headers = {"Content-Type": "application/json"}

    # Optional preflight check for diagnosing URL reachability
    try:
        head_resp = requests.head(video_url_param, timeout=10)
        logger.info(f"🔎 HEAD status: {head_resp.status_code}")
        if head_resp.status_code >= 300:
            logger.error(f"HEAD headers: {head_resp.headers}")
            logger.error(f"HEAD body: {head_resp.text}")
    except Exception as preflight_err:
        logger.warning(f"⚠️ Preflight HEAD to blob failed: {preflight_err}")

    response = requests.post(upload_url, params=params, headers=headers, timeout=60)
    logger.debug(f"Video Indexer request URL: {response.request.url}")
    logger.info(f"🤓 Video Indexer response status: {response.status_code}")

    if response.status_code >= 300:
        logger.error(f"🤔 Video Indexer response body: {response.text}")

    response.raise_for_status()

    result = response.json()
    logger.info(f"🎉 Video submitted successfully. Video ID: {result.get('id')}")
    return result


@app.blob_trigger(arg_name="blob", path=f"{CONTAINER_NAME}/{{blobname}}", connection="AzureWebJobsStorage")
def process_video_blob(blob: func.InputStream):
    """Trigger when a new blob is uploaded; send video to Video Indexer."""
    logger.info("🚀 Blob trigger function processed blob!")
    logger.info(f"Name: {blob.name}")
    logger.info(f"Size: {blob.length} bytes")

    blob_name = blob.name.split("/")[-1] if blob.name else "unknown"
    file_ext = os.path.splitext(blob_name)[1].lower()
    logger.info(f"File extension: {file_ext}")

    if file_ext not in VIDEO_EXTENSIONS:
        logger.warning(f"⛔ Skipping non-video file: {blob_name}")
        return

    blob_sas_url = get_blob_sas_url(blob_name)
    # Log URL without SAS token to avoid leaking secrets
    logger.info(f"🎉 Generated SAS URL for blob: {blob_name}")

    result = submit_video_to_indexer(blob_sas_url, blob_name)
    logger.info(f"🎉 Successfully submitted video '{blob_name}' to Video Indexer")
    logger.info(f"Video Indexer ID: {result.get('id')}")
    logger.info(f"Video Indexer State: {result.get('state')}")
