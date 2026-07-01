import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")

    # ── Docker Compose (direct HTTP) mode ──────────────────────────────────────
    # docker-compose.yml hardcodes these to Docker service names; no .env entry
    # needed for local docker-compose up. Clear/omit to use Foundry mode.
    HOSTED_AGENT_CLINICAL_URL: str = os.getenv("HOSTED_AGENT_CLINICAL_URL", "")
    HOSTED_AGENT_COMPLIANCE_URL: str = os.getenv("HOSTED_AGENT_COMPLIANCE_URL", "")
    HOSTED_AGENT_COVERAGE_URL: str = os.getenv("HOSTED_AGENT_COVERAGE_URL", "")
    HOSTED_AGENT_SYNTHESIS_URL: str = os.getenv("HOSTED_AGENT_SYNTHESIS_URL", "")

    # ── Foundry Hosted Agents mode ──────────────────────────────────────────────
    # On Azure (azd up), Bicep injects AZURE_AI_PROJECT_ENDPOINT and the 4 agent
    # name vars automatically. The backend calls agents via the Foundry Responses
    # API with agent_reference routing; no direct agent URLs are used.
    AI_FOUNDRY_PROJECT_ENDPOINT: str = os.getenv("AI_FOUNDRY_PROJECT_ENDPOINT", "")
    AZURE_AI_PROJECT_ENDPOINT: str = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    HOSTED_AGENT_CLINICAL_NAME: str = os.getenv(
        "HOSTED_AGENT_CLINICAL_NAME", "clinical-reviewer-agent"
    )
    HOSTED_AGENT_COMPLIANCE_NAME: str = os.getenv(
        "HOSTED_AGENT_COMPLIANCE_NAME", "compliance-agent"
    )
    HOSTED_AGENT_COVERAGE_NAME: str = os.getenv(
        "HOSTED_AGENT_COVERAGE_NAME", "coverage-assessment-agent"
    )
    HOSTED_AGENT_SYNTHESIS_NAME: str = os.getenv(
        "HOSTED_AGENT_SYNTHESIS_NAME", "synthesis-agent"
    )

    HOSTED_AGENT_TIMEOUT_SECONDS: float = float(
        os.getenv("HOSTED_AGENT_TIMEOUT_SECONDS", "180")
    )

    # ── CMS-0057 / Da Vinci standards layer (PRD items-to-implement/PRD.md) ──────
    # Policy packs are static, human-reviewed payer/plan/procedure requirement
    # sets loaded from disk. No live payer API is called. The layer is optional
    # and the review pipeline degrades gracefully to runtime search when off.
    # Master switch for the standards-aligned (CRD/DTR/PAS) assessment block.
    ENABLE_STANDARDS_LAYER: bool = os.getenv("ENABLE_STANDARDS_LAYER", "true").lower() == "true"
    ENABLE_POLICY_PACKS: bool = os.getenv("ENABLE_POLICY_PACKS", "true").lower() == "true"
    # Prepare a PAS-style package preview (never submits; real submission stays off).
    ENABLE_PAS_PREPARE: bool = os.getenv("ENABLE_PAS_PREPARE", "true").lower() == "true"
    # Override the policy-packs directory (defaults to <repo>/policy-packs).
    POLICY_PACKS_DIR: str = os.getenv("POLICY_PACKS_DIR", "")

    # Optional auth/header for specific direct-HTTP deployments (rarely needed;
    # Foundry mode uses DefaultAzureCredential automatically).
    HOSTED_AGENT_AUTH_HEADER: str = os.getenv("HOSTED_AGENT_AUTH_HEADER", "Authorization")
    HOSTED_AGENT_AUTH_SCHEME: str = os.getenv("HOSTED_AGENT_AUTH_SCHEME", "Bearer")
    HOSTED_AGENT_AUTH_TOKEN: str = os.getenv("HOSTED_AGENT_AUTH_TOKEN", "")

    # Azure Application Insights (observability)
    APPLICATION_INSIGHTS_CONNECTION_STRING: str = os.getenv(
        "APPLICATION_INSIGHTS_CONNECTION_STRING", ""
    )
    # Debug Console — Foundry-native observability (logstream + App Insights KQL +
    # deep-links). All optional; endpoints degrade gracefully when unset.
    APPLICATION_INSIGHTS_RESOURCE_ID: str = os.getenv("APPLICATION_INSIGHTS_RESOURCE_ID", "")
    AZURE_SUBSCRIPTION_ID: str = os.getenv("AZURE_SUBSCRIPTION_ID", "")
    AZURE_RESOURCE_GROUP: str = os.getenv("AZURE_RESOURCE_GROUP", "")
    AZURE_AI_PROJECT_ID: str = os.getenv("AZURE_AI_PROJECT_ID", "")  # ARM id for portal deep-link

    @property
    def foundry_project_endpoint(self) -> str:
        endpoint = (
            self.AI_FOUNDRY_PROJECT_ENDPOINT
            or self.AZURE_AI_PROJECT_ENDPOINT
            or ""
        ).rstrip("/")

        return endpoint.replace(
            ".cognitiveservices.azure.com",
            ".services.ai.azure.com",
        )


settings = Settings()
