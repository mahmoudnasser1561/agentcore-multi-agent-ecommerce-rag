"""
config.py
=========
Resource configuration for the NovaMart multi-agent system, read from
environment variables (.env locally, runtime env vars once deployed).

DynamoDB table names, the IAM execution role and the log group come from
your own foundation-infrastructure deployment; see docs/deployment.md. This
module never hardcodes an AWS account ID or resource ARN.
"""

import os

from dotenv import load_dotenv

load_dotenv()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "novamart-agentcore")

ORCHESTRATOR_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
WORKER_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

GUARDRAIL_NAME = f"{PROJECT_NAME}-guardrail"
GUARDRAIL_BLOCKED_TOPICS = ["competitor products", "pricing negotiations", "legal threats"]

AGENTCORE_RUNTIME_NAME = f"{PROJECT_NAME}-runtime".replace("-", "_")
MEMORY_NAME = f"{PROJECT_NAME}-memory".replace("-", "_")

# Filled in by the deploy pipeline / your own .env — never required for import.
AGENTCORE_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "")
RETURNS_KB_ID = os.environ.get("RETURNS_KB_ID", "")
SHIPPING_KB_ID = os.environ.get("SHIPPING_KB_ID", "")
WARRANTY_KB_ID = os.environ.get("WARRANTY_KB_ID", "")


class ConfigError(RuntimeError):
    """A required AWS resource identifier could not be resolved."""


_export_cache: dict | None = None


def _exports() -> dict:
    """CloudFormation exports from your own foundation stack, cached after the
    first lookup. Importing this module makes no AWS calls — only resolving
    one of the lazy attributes below does."""
    global _export_cache
    if _export_cache is not None:
        return _export_cache
    import boto3
    from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

    try:
        cf = boto3.client("cloudformation", region_name=AWS_REGION)
        _export_cache = {
            e["Name"]: e["Value"]
            for page in cf.get_paginator("list_exports").paginate()
            for e in page["Exports"]
        }
    except NoCredentialsError as exc:
        raise ConfigError("No AWS credentials configured — run `aws configure`.") from exc
    except EndpointConnectionError as exc:
        raise ConfigError(f"Cannot reach CloudFormation in {AWS_REGION}: {exc}") from exc
    except ClientError as exc:
        raise ConfigError(f"CloudFormation ListExports failed: {exc}") from exc
    return _export_cache


def _export(name: str) -> str:
    value = _exports().get(f"{PROJECT_NAME}-{name}")
    if not value:
        raise ConfigError(
            f"Export '{PROJECT_NAME}-{name}' not found — deploy the foundation "
            f"stack first (see docs/deployment.md)."
        )
    return value


# Resolved lazily (PEP 562) so importing this module needs no AWS credentials.
_LAZY = {
    "ACCOUNT_ID": lambda: __import__("boto3").client(
        "sts", region_name=AWS_REGION
    ).get_caller_identity()["Account"],
    "ORDERS_TABLE": lambda: _export("OrdersTable"),
    "CUSTOMERS_TABLE": lambda: _export("CustomersTable"),
    "WORKFLOW_STATE_TABLE": lambda: _export("WorkflowStateTable"),
    "AGENTCORE_ROLE_ARN": lambda: _export("AgentCoreRoleArn"),
    "AGENT_LOG_GROUP": lambda: _export("AgentLogGroup"),
}


def __getattr__(name: str):
    if name in _LAZY:
        value = _LAZY[name]()
        globals()[name] = value  # cache for subsequent access
        return value
    raise AttributeError(f"module 'config' has no attribute {name!r}")
