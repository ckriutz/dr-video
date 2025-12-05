from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
    ComplexField
)

def create_video_search_index(search_endpoint, index_name, credential):
    """Create a search index for video insights."""
    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
    
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="videoId", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="name", type=SearchFieldDataType.String),
        SearchableField(name="description", type=SearchFieldDataType.String),
        SearchableField(name="transcript", type=SearchFieldDataType.String),
        ComplexField(
            name="transcriptEntries",
            collection=True,
            fields=[
                SearchableField(name="text", type=SearchFieldDataType.String),
                SimpleField(name="startSeconds", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SimpleField(name="endSeconds", type=SearchFieldDataType.Double, filterable=True, sortable=True),
                SimpleField(name="timeRange", type=SearchFieldDataType.String),
            ],
        ),
        SearchableField(name="keywords", type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True, facetable=True),
        SearchableField(name="topics", type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True, facetable=True),
        SearchableField(name="faces", type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True),
        SearchableField(name="labels", type=SearchFieldDataType.Collection(SearchFieldDataType.String), filterable=True, facetable=True),
        SearchableField(name="ocr", type=SearchFieldDataType.String),
        SimpleField(name="duration", type=SearchFieldDataType.Double, filterable=True, sortable=True),
        SimpleField(name="sourceUrl", type=SearchFieldDataType.String),
        SimpleField(name="thumbnailUrl", type=SearchFieldDataType.String),
        SimpleField(name="indexedAt", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
    ]
    
    index = SearchIndex(name=index_name, fields=fields)
    index_client.create_or_update_index(index)
    return index