"""Service authentication and tenant authorization for integration/data APIs."""
import hmac
import os

from fastapi import Header, HTTPException


def _bearer(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    return authorization[7:]


def service_context(authorization: str | None = Header(default=None),
                    x_org_code: str | None = Header(default=None, alias="X-Org-Code")):
    expected = os.getenv("DP_INBOUND_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Inbound service authentication is not configured")
    if not hmac.compare_digest(_bearer(authorization), expected):
        raise HTTPException(status_code=401, detail="Invalid service credentials")
    allowed = {item.strip() for item in os.getenv("DP_INBOUND_ALLOWED_ORGS", "").split(",") if item.strip()}
    if not x_org_code:
        raise HTTPException(status_code=400, detail="X-Org-Code required")
    if "*" not in allowed and x_org_code not in allowed:
        raise HTTPException(status_code=403, detail="Organization is outside credential scope")
    return x_org_code


def authorize_org(context_org, resource_org):
    if context_org != resource_org and os.getenv("DP_INBOUND_ALLOWED_ORGS") != "*":
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
