import os
import logging
import json

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from typing import Any, Dict, List, Optional

import azure.functions as func
import requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.search.documents import SearchClient
from pprint import pprint
from VideoIndexerClient import VideoIndexerClient
from consts import Consts
from create_index import create_video_search_index

app = func.FunctionApp()

# Configuration from environment variables
VIDEO_INDEXER_ACCOUNT_ID = os.environ.get("AZURE_VIDEO_INDEXER_ACCOUNT_ID")
VIDEO_INDEXER_LOCATION = os.environ.get("AZURE_VIDEO_INDEXER_LOCATION", "westus3")
MANAGED_IDENTITY_CLIENT_ID = os.environ.get("MANAGED_IDENTITY_CLIENT_ID")
STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME")
BLOB_SAS_VERSION = os.environ.get("AZURE_STORAGE_SAS_VERSION", "2022-11-02")
AZURE_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME")
FUNCTION_APP_URL = os.environ.get("FUNCTION_APP_URL")
AZURE_VIDEO_INDEXER_ACCOUNT_NAME = os.environ.get("AZURE_VIDEO_INDEXER_ACCOUNT_NAME")
AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID")
AZURE_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP")

# Constants
CONTAINER_NAME = "dr-videos"
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".wmv", ".mkv", ".webm", ".flv"}

# Azure Video Indexer API endpoints
#VIDEO_INDEXER_API_URL = "https://api.videoindexer.ai"
#ARM_ACCESS_TOKEN_URL = (
#    "https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/"
#    "{resource_group}/providers/Microsoft.VideoIndexer/accounts/{account_name}/"
#    "generateAccessToken?api-version=2024-01-01"
#)

# Shared credential instance (supports Managed Identity in Azure, developer creds locally)
_credential = DefaultAzureCredential(managed_identity_client_id=MANAGED_IDENTITY_CLIENT_ID)

# Create Consts instance for Video Indexer client
consts_config = Consts(
    ApiVersion="2024-01-01",
    ApiEndpoint="https://api.videoindexer.ai",
    AzureResourceManager="https://management.azure.com",
    AccountName=AZURE_VIDEO_INDEXER_ACCOUNT_NAME,
    ResourceGroup=AZURE_RESOURCE_GROUP,
    SubscriptionId=AZURE_SUBSCRIPTION_ID
)

logger = logging.getLogger(__name__)

logger.info("Azure Search Endpoint: %s", AZURE_SEARCH_ENDPOINT)
logger.info("Azure Search Index Name: %s", AZURE_SEARCH_INDEX_NAME)

create_video_search_index(AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_INDEX_NAME, _credential)

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
    logger.info(f"🔍 SAS URL: {account_url}/{CONTAINER_NAME}/{blob_name}?{sas_token}")
    return f"{account_url}/{CONTAINER_NAME}/{blob_name}?{sas_token}"

def _time_to_seconds(value: Optional[str]) -> Optional[float]:
    """Convert a hh:mm:ss.f time string to seconds (float)."""
    if value is None:
        return None
    try:
        parts = str(value).split(":")
        parts = [float(p) for p in parts]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
        return hours * 3600 + minutes * 60 + seconds
    except Exception:
        return None
    
def _extract_transcript_entries(index_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract transcript entries with timestamps and speaker info."""
    transcripts: List[Dict[str, Any]] = []
    
    # Get transcript from videos[0].insights
    videos = index_json.get("videos") or []
    if videos and videos[0].get("insights"):
        transcript_data = videos[0]["insights"].get("transcript", [])
        
        for entry in transcript_data:
            text = entry.get("text")
            if not text:
                continue
            
            instances = entry.get("instances") or []
            start = end = None
            if instances:
                start = instances[0].get("start") or instances[0].get("adjustedStart")
                end = instances[0].get("end") or instances[0].get("adjustedEnd")
            
            # Build entry, only including fields with valid values
            transcript_entry = {"text": text}
            
            start_seconds = _time_to_seconds(start)
            if start_seconds is not None:
                transcript_entry["startSeconds"] = start_seconds
            
            end_seconds = _time_to_seconds(end)
            if end_seconds is not None:
                transcript_entry["endSeconds"] = end_seconds
            
            speaker_id = entry.get("speakerId")
            if speaker_id is not None:
                transcript_entry["speakerId"] = speaker_id
            
            confidence = entry.get("confidence")
            if confidence is not None:
                transcript_entry["confidence"] = confidence
            
            transcripts.append(transcript_entry)
    
    return transcripts

def _collect_names(items: List[Dict[str, Any]], name_field: str = "name") -> List[str]:
    """Extract names from a list of items."""
    names: List[str] = []
    for item in items or []:
        name = item.get(name_field)
        if name:
            names.append(name)
    return names

def build_search_document(index_json: Dict[str, Any]) -> Dict[str, Any]:
    """Map Video Indexer insights JSON into the Azure AI Search document shape."""
    video_id = index_json.get("id")
    videos = index_json.get("videos", [])
    video_insights = videos[0].get("insights", {}) if videos else {}
    summarized = index_json.get("summarizedInsights", {})
    
    # Extract transcript entries
    transcript_entries = _extract_transcript_entries(index_json)
    transcript_text = " ".join([t.get("text", "") for t in transcript_entries]).strip() or None
    
    # Extract keywords, topics, faces, labels
    keywords = _collect_names(summarized.get("keywords", []))
    topics = _collect_names(summarized.get("topics", []))
    faces = _collect_names(summarized.get("faces", []))
    labels = _collect_names(summarized.get("labels", []))
    
    # Extract OCR text
    ocr_entries = video_insights.get("ocr", [])
    ocr_texts = [entry.get("text") for entry in ocr_entries if entry.get("text")]
    ocr_text = " ".join(ocr_texts) if ocr_texts else None
    
    # Get duration
    duration = index_json.get("durationInSeconds") or summarized.get("duration", {}).get("seconds")
    
    # Get speaker count
    speakers = video_insights.get("speakers", [])
    speaker_count = len(speakers) if speakers else 0
    
    # Get language
    language = video_insights.get("language") or video_insights.get("sourceLanguage")
    
    # Get published URL and thumbnail
    published_url = videos[0].get("publishedUrl") if videos else None
    thumbnail_id = videos[0].get("thumbnailId") if videos else None
    
    # Build the document - match the ACTUAL Azure Search index schema
    # Note: transcriptEntries is Collection(ComplexType), others are Edm.String
    document = {
        "id": video_id,
        "videoId": video_id,
        "name": index_json.get("name"),
        "transcript": transcript_text,
        "transcriptEntries": transcript_entries,  # Keep as array - schema expects Collection(ComplexType)
        "keywords": ", ".join(keywords) if keywords else "",  # Convert to string - schema expects Edm.String
        "topics": ", ".join(topics) if topics else "",  # Convert to string - schema expects Edm.String
        "faces": ", ".join(faces) if faces else "",  # Convert to string - schema expects Edm.String
        "labels": ", ".join(labels) if labels else "",  # Convert to string - schema expects Edm.String
        "ocr": ocr_text,
        "duration": duration,
        "created": index_json.get("created"),
        "language": language,
        "speakerCount": speaker_count,
        "publishedUrl": published_url,
        "thumbnailId": thumbnail_id,
        "indexedAt": datetime.now(timezone.utc).isoformat(),
    }
    
    return document


def upload_to_search_index(index_json: Dict[str, Any]) -> bool:
    """Upload video insights to Azure AI Search."""
    if not AZURE_SEARCH_ENDPOINT or not AZURE_SEARCH_INDEX_NAME:
        logger.warning("⚠️ Azure Search not configured - skipping upload")
        return False
    
    try:
        # Build the search document
        document = build_search_document(index_json)
        video_id = document["id"]
        
        logger.info(f"📄 Building search document for video {video_id}")
        
        # Create search client and upload
        search_client = SearchClient(
            endpoint=AZURE_SEARCH_ENDPOINT,
            index_name=AZURE_SEARCH_INDEX_NAME,
            credential=_credential
        )
        
        result = search_client.upload_documents(documents=[document])
        
        # Check if upload succeeded
        if result and result[0].succeeded:
            logger.info(f"✅ Successfully uploaded video {video_id} to search index")
            return True
        else:
            error_msg = result[0].error_message if result else "Unknown error"
            logger.error(f"❌ Failed to upload video {video_id}: {error_msg}")
            return False
            
    except Exception as e:
        logger.exception(f"❌ Error uploading to search index: {e}")
        return False


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

    # Download the blob to a temporary file
    logger.info(f"📥 Downloading blob: {blob_name}")
    blob_client = BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
        credential=_credential
    ).get_blob_client(container=CONTAINER_NAME, blob=blob_name)
    
    # Create temp file path
    import tempfile
    temp_dir = tempfile.gettempdir()
    temp_file_path = os.path.join(temp_dir, blob_name)
    
    # Download blob to temp file
    with open(temp_file_path, "wb") as temp_file:
        download_stream = blob_client.download_blob()
        temp_file.write(download_stream.readall())
    
    logger.info(f"✅ Downloaded blob to: {temp_file_path}")

    # create Video Indexer Client
    client = VideoIndexerClient()

    # Get access tokens (arm and Video Indexer account)
    client.authenticate(consts_config)
    client.get_account()

    ExcludedAI = []
    
    # Upload the file directly instead of using URL
    logger.info(f"📤 Uploading file to Video Indexer: {blob_name}")
    video_id = client.file_upload(temp_file_path, blob_name, ExcludedAI)
    
    # Clean up temp file
    os.remove(temp_file_path)
    logger.info(f"🧹 Cleaned up temp file: {temp_file_path}")

    logger.info(f"🎉 Video uploaded successfully. Video ID: {video_id}.")
    logger.info("⏳ Waiting for Video Indexer to process the video...")
    result = client.wait_for_index(video_id)

    if result:
        logger.info("🎉 Video processing completed successfully.")
        
        # Get the video insights
        insights = client.get_video(video_id)
        
        # Upload to Azure AI Search
        upload_to_search_index(insights)
    else:
        logger.error("🤔 Video processing failed.")
        return
    
    # Now that we have the results of the video processing, we can add it to the search index.
