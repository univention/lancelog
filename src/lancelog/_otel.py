# SPDX-License-Identifier: AGPL-3.0-only

"""OpenTelemetry integration. Imported only when an OpenTelemetry option is enabled."""

from opentelemetry import trace


def trace_context() -> dict[str, str]:
    """Return trace_id, span_id and trace_flags of the active span, or an empty dict if there is none."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "trace_flags": format(int(ctx.trace_flags), "02x"),
    }
