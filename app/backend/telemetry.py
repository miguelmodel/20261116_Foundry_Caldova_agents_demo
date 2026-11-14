"""Best-effort cloud export; local runs always receive a real OTel trace ID."""

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from .settings import Settings, env

log = logging.getLogger(__name__)
_local_provider = TracerProvider()


def run_tracer():
    provider = trace.get_tracer_provider()
    if isinstance(provider, trace.ProxyTracerProvider):
        provider = _local_provider
    return provider.get_tracer("caldova.control_tower")


def configure_tracing(settings: Settings) -> None:
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    os.environ.setdefault("OTEL_SERVICE_NAME", env("OTEL_SERVICE_NAME", "caldova-control-tower-backend"))
    try:
        from azure.ai.projects.telemetry import AIProjectInstrumentor

        AIProjectInstrumentor().instrument(enable_trace_context_propagation=True)
        if not settings.configured:
            log.warning("Foundry endpoint is not configured; traces remain local until Azure is configured.")
            return
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from azure.monitor.opentelemetry import configure_azure_monitor

        with DefaultAzureCredential(process_timeout=30) as credential:
            with AIProjectClient(
                endpoint=settings.endpoint,
                credential=credential,
                connection_timeout=5,
                read_timeout=5,
                retry_total=0,
            ) as project:
                connection_string = project.telemetry.get_application_insights_connection_string()
        if connection_string:
            configure_azure_monitor(connection_string=connection_string)
            log.info("Application Insights tracing configured.")
        else:
            log.warning("No Application Insights connection string is linked to the project.")
    except Exception as exc:
        log.warning("Cloud tracing is unavailable; the application will continue: %s", exc)
