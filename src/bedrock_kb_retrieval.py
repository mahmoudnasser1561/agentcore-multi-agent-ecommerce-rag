"""
bedrock_kb_retrieval.py
========================
Thin wrapper over the Bedrock Knowledge Bases Retrieve API, used by the
PolicyAgent's three retriever sub-agents.
"""

import boto3

import config

_client = boto3.client("bedrock-agent-runtime", region_name=config.AWS_REGION)


def retrieve_from_knowledge_base(knowledge_base_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Return the top-k raw retrieval results for `query` from one knowledge base."""
    if not knowledge_base_id:
        return []
    response = _client.retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
    )
    return response.get("retrievalResults", [])


def format_kb_results(results: list[dict]) -> str:
    """Join retrieved passage texts into one string for the model to read."""
    passages = [r.get("content", {}).get("text", "").strip() for r in results]
    passages = [p for p in passages if p]
    return "\n---\n".join(passages) if passages else "No relevant passages were found."
