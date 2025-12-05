"""
Azure Function - Video Indexer Blob Trigger
Triggers when a new video file is uploaded to the dr-videos container
and sends it to Azure AI Video Indexer for processing.
"""

import os
import logging
import azure.functions as func
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
import requests

app = func.FunctionApp()

# Configuration from environment variables
VIDEO_INDEXER_ACCOUNT_ID = os.environ.get("AZURE_VIDEO_INDEXER_ACCOUNT_ID")
VIDEO_INDEXER_LOCATION = os.environ.get("AZURE_VIDEO_INDEXER_LOCATION", "westus3")
MANAGED_IDENTITY_CLIENT_ID = os.environ.get("MANAGED_IDENTITY_CLIENT_ID")
STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME")
BLOB_SAS_VERSION = os.environ.get("AZURE_STORAGE_SAS_VERSION", "2022-11-02")

# Azure Video Indexer API endpoints
VIDEO_INDEXER_API_URL = "https://api.videoindexer.ai"
ARM_ACCESS_TOKEN_URL = "https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.VideoIndexer/accounts/{account_name}/generateAccessToken?api-version=2024-01-01"

logging.basicConfig(level=logging.INFO)

def get_video_indexer_access_token() -> str:
    """
    Get an access token for Azure Video Indexer using managed identity.
    """
    logging.info("Getting Video Indexer access token using Managed Identity.")
    try:
        # Use DefaultAzureCredential which supports both Managed Identity (when deployed)
        # and developer credentials (when running locally)
        credential = DefaultAzureCredential(managed_identity_client_id=MANAGED_IDENTITY_CLIENT_ID)
        
        # Get ARM token first
        arm_token = credential.get_token("https://management.azure.com/.default").token
        
        # Use ARM token to get Video Indexer access token
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
        resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
        account_name = os.environ.get("AZURE_VIDEO_INDEXER_ACCOUNT_NAME")
        
        headers = {
            "Authorization": f"Bearer {arm_token}",
            "Content-Type": "application/json"
        }
        
        body = {
            "permissionType": "Contributor",
            "scope": "Account"
        }
        
        url = ARM_ACCESS_TOKEN_URL.format(
            subscription_id=subscription_id,
            resource_group=resource_group,
            account_name=account_name
        )
        
        response = requests.post(url, headers=headers, json=body)
        response.raise_for_status()
        
        logging.info("🎉 Obtained Video Indexer access token successfully.")
        return response.json().get("accessToken")
        
    except Exception as e:
        logging.error(f"⛔ Failed to get Video Indexer access token: {str(e)}")
        raise


def get_blob_sas_url(blob_name: str) -> str:
    """
    Generate a SAS URL for the blob to be used by Video Indexer.
    """
    from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
    from datetime import datetime, timedelta, timezone

    logging.info(f"🔧 Generating SAS URL for blob: {blob_name}")
    
    try:
        # Use DefaultAzureCredential which supports both Managed Identity (when deployed)
        # and developer credentials (when running locally)
        credential = DefaultAzureCredential(managed_identity_client_id=MANAGED_IDENTITY_CLIENT_ID)
        
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        blob_service_client = BlobServiceClient(account_url=account_url, credential=credential)
        
        # Get user delegation key for SAS
        now = datetime.now(timezone.utc)
        skew = timedelta(minutes=5)
        user_delegation_key = blob_service_client.get_user_delegation_key(
            key_start_time=now - skew,
            key_expiry_time=now + timedelta(hours=2)
        )
        
        # Generate SAS token (pin to a storage service version for compatibility)
        sas_token = generate_blob_sas(
            account_name=STORAGE_ACCOUNT_NAME,
            container_name="dr-videos",
            blob_name=blob_name,
            user_delegation_key=user_delegation_key,
            permission=BlobSasPermissions(read=True),
            expiry=now + timedelta(hours=2),
            start=now - skew,
            version=BLOB_SAS_VERSION,
        )
        
        logging.info("🎉 Generated SAS URL for blob successfully.")
        return f"{account_url}/dr-videos/{blob_name}?{sas_token}"
        
    except Exception as e:
        logging.error(f"⛔ Failed to generate SAS URL: {str(e)}")
        raise


def submit_video_to_indexer(video_url: str, video_name: str) -> dict:
    """
    Submit a video to Azure AI Video Indexer for processing.
    """
    logging.info(f"🔧 Submitting video '{video_name}' to Azure AI Video Indexer.")
    try:
        access_token = get_video_indexer_access_token()

        # Prepare the upload URL
        upload_url = f"{VIDEO_INDEXER_API_URL}/{VIDEO_INDEXER_LOCATION}/Accounts/{VIDEO_INDEXER_ACCOUNT_ID}/Videos"

        # Let requests handle encoding; pass a decoded SAS URL so it's encoded exactly once
        from urllib.parse import unquote
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

        headers = {
            "Content-Type": "application/json"
        }

        # Optional preflight check to help diagnose URL reachability
        try:
            head_resp = requests.head(video_url_param, timeout=10)
            logging.info(f"🔎 HEAD status: {head_resp.status_code}")
            if head_resp.status_code >= 300:
                logging.error(f"HEAD headers: {head_resp.headers}")
                logging.error(f"HEAD body: {head_resp.text}")
        except Exception as preflight_err:
            logging.warning(f"⚠️ Preflight HEAD to blob failed: {preflight_err}")

        response = requests.post(upload_url, params=params, headers=headers, timeout=30)

        logging.debug(f"Video Indexer request URL: {response.request.url}")

        # Log non-success responses for debugging
        logging.info(f"🤓 Video Indexer response status: {response.status_code}")
        if response.status_code >= 300:
            logging.error(f"🤔 Video Indexer response body: {response.text}")

        response.raise_for_status()

        result = response.json()
        logging.info(f"🎉 Video submitted successfully. Video ID: {result.get('id')}")

        return result

    except Exception as e:
        logging.error(f"⛔ Failed to submit video to indexer: {str(e)}")
        raise


@app.blob_trigger(arg_name="blob", path="dr-videos/{blobname}", connection="AzureWebJobsStorage")
def process_video_blob(blob: func.InputStream):
    """
    Azure Function triggered when a new blob is uploaded to dr-videos container.
    Sends the video to Azure AI Video Indexer for processing.
    """

    # We got a file! Lets get some info about it.
    logging.info(f"🚀 Blob trigger function processed blob!")
    logging.info(f"Name: {blob.name}")
    logging.info(f"Size: {blob.length} bytes")

    # Now, lets make sure it's a video file before we send it to Video Indexer.
    video_extensions = [".mp4", ".avi", ".mov", ".wmv", ".mkv", ".webm", ".flv"]
    blob_name = blob.name.split("/")[-1] if blob.name else "unknown"
    file_ext = os.path.splitext(blob_name)[1].lower()
    logging.info(f"File extension: {file_ext}")

    if file_ext not in video_extensions:
        logging.warning(f"⛔ Skipping non-video file: {blob_name}")
        return
    
    # Lets get the SAS URL for the blob
    blob_sas_url = get_blob_sas_url(blob_name)
    logging.info(f"🎉 Generated SAS URL for blob: {blob_name} with url: {blob_sas_url}")

    # Submit to Video Indexer
    try:
        result = submit_video_to_indexer(blob_sas_url, blob_name)
        logging.info(f"🎉 Successfully submitted video '{blob_name}' to Video Indexer")
        logging.info(f"Video Indexer ID: {result.get('id')}")
        logging.info(f"Video Indexer State: {result.get('state')}")
    except Exception as e:
        logging.error(f"⛔ Error processing video '{blob_name}': {str(e)}")
        raise


    
    
    #try:
        # Generate SAS URL for the blob
    #    video_url = get_blob_sas_url(blob_name)
    #    print(f"Generated SAS URL for blob: {blob_name}")
        
        # Submit to Video Indexer
    #    result = submit_video_to_indexer(video_url, blob_name)

    #    print(f"Successfully submitted video '{blob_name}' to Video Indexer")
    #    print(f"Video Indexer ID: {result.get('id')}")
    #    print(f"Video Indexer State: {result.get('state')}")

    #except Exception as e:
    #    print(f"Error processing video '{blob_name}': {str(e)}")
    #    raise
