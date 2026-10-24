# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "lancelog[otel]",
#     "asgi-correlation-id",
#     "fastapi",
#     "uvicorn",
#     "opentelemetry-instrumentation-fastapi",
# ]
#
# [tool.uv.sources]
# lancelog = { path = "../..", editable = true }
# ///
"""Example FastAPI app that logs to stdout and to VictoriaLogs over OTLP."""

import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider

from lancelog import DataSectionOptions, OtelOptions, logger, setup_logging, uvicorn_log_config


# Spans only need valid IDs, so no span exporter is configured.
trace.set_tracer_provider(TracerProvider())

setup_logging(
    data_options=DataSectionOptions(trace_context=True),
    request_id_func=correlation_id.get,
    otel=OtelOptions(
        level="DEBUG",
        endpoint="http://localhost:9428/insert/opentelemetry/v1/logs",
        service_name="lancelog-example",
    ),
)

app = FastAPI()
# Without validator=None, IDs that are not UUID4 strings would be replaced by generated ones.
app.add_middleware(CorrelationIdMiddleware, validator=None)
FastAPIInstrumentor.instrument_app(app)


def index() -> dict[str, str]:
    logger.bind(user="alice", items=3).info("Handled request")
    return {"status": "ok"}


def error() -> dict[str, str]:
    try:
        int("not a number")
    except ValueError:
        logger.exception("Calculation failed")
    return {"status": "error logged"}


app.add_api_route("/", index)
app.add_api_route("/error", error)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_config=uvicorn_log_config())
