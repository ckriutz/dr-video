from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
    ComplexField
)

def create_video_search_index(search_endpoint, index_name, credential):
        # NOTE: The fields 'keywords', 'topics', 'faces', and 'labels' are defined as simple strings (not collections)
        # to match the actual deployed Azure Search index schema. This is due to a previous issue where these fields
        # were incorrectly defined as arrays, causing upload errors. See troubleshooting notes in the project history.
        # If you wish to use arrays for these fields, you must update both the index and the function app mapping logic.
    """Create a search index for video insights."""
    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
    
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="videoId", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="name", type=SearchFieldDataType.String),
        SearchableField(name="transcript", type=SearchFieldDataType.String),
        ComplexField(
            name="transcriptEntries",
            collection=True,
            fields=[
                SearchableField(name="text", type=SearchFieldDataType.String),
                SimpleField(name="startSeconds", type=SearchFieldDataType.Double, filterable=True),
                SimpleField(name="endSeconds", type=SearchFieldDataType.Double, filterable=True),
                SimpleField(name="speakerId", type=SearchFieldDataType.Int32, filterable=True),
                SimpleField(name="confidence", type=SearchFieldDataType.Double, filterable=True),
            ],
        ),
        SearchableField(name="keywords", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="topics", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="faces", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="labels", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="ocr", type=SearchFieldDataType.String),
        SimpleField(name="duration", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SimpleField(name="created", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SimpleField(name="language", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="speakerCount", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="publishedUrl", type=SearchFieldDataType.String),
        SimpleField(name="thumbnailId", type=SearchFieldDataType.String),
        SimpleField(name="indexedAt", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
    ]
    
    index = SearchIndex(name=index_name, fields=fields)
    index_client.create_or_update_index(index)
    return index