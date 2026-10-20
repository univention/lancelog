# OpenTelemetry example

A FastAPI application that logs to stdout and sends the same records over OTLP to
[VictoriaLogs](https://docs.victoriametrics.com/victorialogs/), which shows them in a web UI.
It demonstrates `DataSectionOptions(trace_context=True)` and `OtelOptions`
(see the [main README](../../README.md#opentelemetry-trace-and-span-ids)).

This is a demo: no authentication, persistence or TLS.

## Run it

Requires Docker and [uv](https://docs.astral.sh/uv/).
Run all commands from the repository root.

1. Start VictoriaLogs.
   Either with Compose:

   ```shell
   docker compose -f docs/otel-example/compose.yaml up
   ```

   or with a single command:

   ```shell
   docker run --rm -p 9428:9428 victoriametrics/victoria-logs:v1.30.0
   ```

2. Start the application.
   `uv` reads the dependencies from the inline metadata in `app.py` and installs Lancelog from this checkout.

   ```shell
   uv run docs/otel-example/app.py
   ```

3. Create log records.
   The `X-Request-ID` header sets the request ID of the log records
   ([asgi-correlation-id](https://github.com/snok/asgi-correlation-id) reads it);
   without the header, the middleware generates one.

   ```shell
   curl -H 'X-Request-ID: demo-request-1' localhost:8000/
   curl -H 'X-Request-ID: demo-request-2' localhost:8000/error
   ```

4. Open <http://localhost:9428/select/vmui/> and run the query `service.name:"lancelog-example"`.
   The records appear a few seconds after the requests, because the exporter sends batches after 1 second by default.

To query from the command line:

```shell
curl localhost:9428/select/logsql/query -d 'query=service.name:"lancelog-example"'
```

## What to look for

- The record "Handled request" has the attributes `user` and `items` (from `logger.bind()`),
  and `trace_id` and `span_id` of the span that `opentelemetry-instrumentation-fastapi` created for the request.
- The record "Calculation failed" has the fields `exception.type`, `exception.message` and `exception.stacktrace`.
- Uvicorn's access log records "HTTP request." have the fields `http.request.method`, `url.path`
  and `http.response.status_code` (from `uvicorn_log_config()`).
- Both records have `request_id` set to the value of the `X-Request-ID` header (`demo-request-1`, `demo-request-2`).
  `CorrelationIdMiddleware` stores the header in a context variable,
  and the app passes its getter to `setup_logging(request_id_func=correlation_id.get)`.
  Records logged outside a request, such as "Configured logging.", have `request_id` `-`.
- Every record has `code.file.path`, `code.function.name` and `code.line.number`,
  the severity in `severity`, and the resource attribute `service.name`.
- The stdout of the application shows the same records as JSON,
  with `request_id`, `trace_id`, `span_id` and `trace_flags` in `extra`.

Stop the application with Ctrl+C.
Lancelog flushes pending records at exit.
Stop VictoriaLogs with Ctrl+C (or `docker compose -f docs/otel-example/compose.yaml down`).
