# The Indigo Video AI Machine

The goal of this repo is to show how videos can be automatically transcribed, and then the transcription is sent to search, for used as part of a RAG pattern.

## Tech Used
- Azure Blob Storage
- Function App (to be triggered from a new blob in blob storage)
- Azure AI Video indexer
- Azure AI Search

## How to get started

There is a bicep template that should help show the resources that are created.

## Security

In general, the security on this is high, and a managed identity is used and given permission to the resources. It is advised to continue to use this method.