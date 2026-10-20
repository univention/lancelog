# ♞ Lancelog ♞ - Setup logging like a hero 🌟

_A library that helps developers to set up logging in their applications in an easy and consistent way._

[![Coverage](https://sonarqube.knut.univention.de/api/project_badges/measure?project=univention_dev_libraries_lancelog_0c5cb975-55c2-432a-b96e-c1e4666648e5&metric=coverage&token=sqb_d3e918a57a60c02234647b3abcc9fe9e338e57c7)](https://sonarqube.knut.univention.de/dashboard?id=univention_dev_libraries_lancelog_0c5cb975-55c2-432a-b96e-c1e4666648e5)
[![Quality Gate Status](https://sonarqube.knut.univention.de/api/project_badges/measure?project=univention_dev_libraries_lancelog_0c5cb975-55c2-432a-b96e-c1e4666648e5&metric=alert_status&token=sqb_d3e918a57a60c02234647b3abcc9fe9e338e57c7)](https://sonarqube.knut.univention.de/dashboard?id=univention_dev_libraries_lancelog_0c5cb975-55c2-432a-b96e-c1e4666648e5)
[![Python version](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)

The [Loguru](https://loguru.readthedocs.io) and the Python logging libraries are set up
according to our ADRs (see section [#Background](#Background)).

---

[[_TOC_]]

## Installation

The `pip` package is hosted in GitLab.
Install it like this:

```shell
pip install lancelog \
  --index-url https://git.knut.univention.de/api/v4/projects/1449/packages/pypi/simple
```

### Usage in pyproject.toml

To reference _lancelog_ in your projects `pyproject.toml`:

When using Poetry:

```toml
[[tool.poetry.source]]
name = "lancelog"
url = "https://git.knut.univention.de/api/v4/projects/1449/packages/pypi/simple"
priority = "supplemental"
```

When using uv:

```toml
[[tool.uv.index]]
url = "https://git.knut.univention.de/api/v4/projects/1449/packages/pypi/simple"
```

## Usage

1. Call `setup_logging()` in your application's startup routine.
2. Import the Loguru logger in each module: `from lancelog import logger`
3. Use the logger: `logger.info("Hello")`

Calls to the Python logging libraries are intercepted and redirected into Loguru.
But, while mixed use of Loguru and Python logging is possible, it is discouraged,
because Python logging doesn't provide structured logging (see section [Structured logging](#Structured%20logging)).

### Initialization

```python
# main.py

from lancelog import setup_logging

setup_logging()
```

### Configuration

All configuration is done in the call to `setup_logging()`:

```python
def setup_logging(
    sink: TextIO = sys.stdout,
    fmt: str | None = None,
    level: int | str | LogLevel = LogLevel.INFO,
    filter: dict[str, str | bool] | None = None,
    colorize: bool | None = None,
    data_format: DataFormat = DataFormat.LOGFMT,
    syslog_prio_prefix: bool = False,
    enqueue: bool = False,
    request_id_func: Callable[[], str] = None,
    data_options: DataSectionOptions | None = None,
    otel: OtelOptions | None = None,
) -> None:
    """
    Configure logging.

    :param sink: A "file-like object" like `sys.stdout` (default), `sys.stderr`,
        an opened file, or a file path as `str` or `pathlib.Path`.
    :param fmt: The template used to format logged messages. Does not apply for JSON output.
        The default format (`lancelog.LOG_FORMAT_DEFAULT`) in version 0.3.0 conforms to ADR 0010.
        If you wish to use the previous format, set `fmt` to `lancelog.LOG_FORMAT_PRE_v030`.
    :param level: Minimum severity level of messages to send to the sink. Default is "INFO".
    :param filter: Decide for each logged message whether it should be emitted or discarded.
        A mapping from module names to minimum required level.
        The closest parent will be searched in the dict.
        Values are levels (`int`|`str`) or a `bool` to emit or discard unconditionally.
    :param colorize: Whether to use color markups in messages.
        By default, enabled if `sink` is connected to a terminal.
    :param data_format: Encode messages to Logfmt (`DataFormat.LOGFMT`, default) or JSON (`DataFormat.JSON`).
    :param syslog_prio_prefix: Whether to prefix logs with a Syslog priority marker. Disabled by default.
        Syslog and Journald require this to detect the log level of a log message.
        Auto-disabled, if `sink` is connected to a terminal.
    :param bool enqueue: Whether log messages should be sent through a multiprocessing-safe queue to the sink.
        Useful when logging concurrently from multiple processes or threads. Makes logging calls non-blocking.
        Disabled by default.
    :param Callable request_id_func: Function that returns a request ID string to be added to each log record.
        If omitted, the request ID will be `-`.
    :return: None
    :param DataSectionOptions data_options: Optional values added to the data section of every log record.
        When writing JSON, values will be in to the `extra` field.
    :param OtelOptions otel: Also export log records to an OpenTelemetry endpoint over OTLP/HTTP.
        Requires `lancelog[otel]`. Pending records are flushed at exit and when `setup_logging` is called again.
    """
```

Best practice is to use the defaults:

- Logs go to standard output.
  Storing, forwarding, compressing, rotating etc. is the job of the system that starts the application: SystemD, Docker, etc.
- Format logs using [Logfmt](https://brandur.org/logfmt).
  Logfmt and JSON are both machine-parsable, but Logfmt is preferred because it is also human-readable.
  Use JSON only when you connect your logs to an aggregator.
- Set `syslog_prio_prefix` according to the deployment of your application.
  If the logs will be consumed by a system that supports the "sd-daemon prefix" to detect Syslog priorities
  (e.g., Syslog and Journald), enable it, otherwise disable it.

#### Breaking format change in version 0.3.0

While Lancelog's API didn't change,
the modification of the output format can be a breaking change for those that parse log files.

To revert to the old format set `fmt` to `lancelog.LOG_FORMAT_PRE_v030`.

```python
>>> from lancelog import LOG_FORMAT_PRE_v030, logger, setup_logging

# use new default, conforming to ADR 0010 (lancelog.LOG_FORMAT_DEFAULT):
>>> setup_logging()
>>> logger.info("Hello", target="world")
2025-09-16T09:45:09.787+02:00 INFO     [         -] Hello | target=world _source=<stdin>.<module>:1

# use pre-0.3.0 format:
>>> setup_logging(fmt=LOG_FORMAT_PRE_v030)
>>> logger.info("Hello", target="world")
2025-09-16 09:45:13.691 +02:00 |    INFO | <stdin>.<module>:1 | Hello | target=world
```

#### Additional values for the data section

When you write a multithreaded or multiprocess applications,
to comply with [ADR 0010 Log format](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0010-log-messages.md),
you must add the thread or process ID to each log record.

When you configure a parser in your log analyzing software,
you may wish to read the date from the data instead of the header section.

Those and other value can be automatically added to the data section (or the `extra` field in JSON output)
by creating a `DataSectionOptions` object and passing it to `setup_logging()`.

```python
class DataSectionOptions:
    log_level: bool = False  # value: str ("INFO")
    request_id: bool = False  # value: str ("9f5ea85512")
    process_id: bool = False  # value: int (165163)
    source_ref_key: str = "_source"  # name of field for source of log record, value: "{module}.{function}:{line}"
    time: bool = False  # ISO8601 format, value: str
    timestamp: bool = False  # UNIX timestamp, value: float
    thread_id: bool = False  # value: int
    thread_name: bool = False  # value: str
    trace_context: bool = False  # trace_id, span_id, trace_flags of the active span, needs lancelog[otel]
```

By default, all options are deactivated (`source_ref_key` is set to the default).
In the following example they have all been activated:

```python
>>> from lancelog import DataSectionOptions, logger, setup_logging; from uuid import uuid4
>>> options = DataSectionOptions(log_level=True, request_id=True, process_id=True, source_ref_key="SRC", time=True, timestamp=True, thread_id=True, thread_name=True)
>>> setup_logging(request_id_func=lambda: uuid4().hex, data_options=options)
>>> logger.info("Hello", target="world")
2025-09-16T08:58:18.421+02:00 INFO     [ccb6463c5c] Hello | target=world log_level=INFO request_id=ccb6463c5c174e59ac6759cf0fee50bf process_id=1651638 time=2025-09-16T08:58:18.421830+02:00 timestamp=1758005898.42183 thread_id=137915718510400 thread_name=MainThread SRC=<stdin>.<module>:1
```

#### Add a request ID

To add a correlation/request ID to each log record,
set `request_id_func` to a function that accepts no arguments and returns a string.
The string will be shortened to the first 10 characters in the output.
If `request_id_func` is not set, a dash (`-`) will be logged.

```python
>>> from uuid import uuid4
>>> from lancelog import logger, setup_logging

>>> setup_logging(request_id_func=lambda: uuid4().hex)

>>> logger.info("The event", foo="´bar")
2025-09-15T15:49:39.851+02:00     INFO [203fb82039] The event | foo=bar _source=<stdin>.<module>:1
```

When creating HTTP APIs, good experiences exist using the [asgi-correlation-id](https://github.com/snok/asgi-correlation-id) library.
It's easy to tie it into Lancelog:

```python
from asgi_correlation_id import correlation_id

setup_logging(request_id_func=correlation_id.get)
```

#### Move log level from DEBUG to TRACE

The logs of the `http.client` and `urllib3` libraries at `DEBUG` level contain sensitive information.
So, we're lowering all `DEBUG` level output to `TRACE`.

The mechanism is extensible.
If you wish to move the `DEBUG` output of other loggers to `TRACE`:

```python
from lancelog import LOG_LEVEL_DEBUG_TO_TRACE

LOG_LEVEL_DEBUG_TO_TRACE.add("logger_name")
```

```python
>>> import logging
>>> from lancelog import setup_logging, LOG_LEVEL_DEBUG_TO_TRACE
>>> setup_logging(level="DEBUG")

>>> py_logger = logging.getLogger("noisy_lib")
>>> py_logger.debug("Private data")
2025-09-15T16:51:40.450+02:00 DEBUG    [         -] Private data | _source=<stdin>.<module>:1

>>> LOG_LEVEL_DEBUG_TO_TRACE.add("noisy_lib")
>>> py_logger.debug("Private data")

>>> setup_logging(level="TRACE")
>>> py_logger.debug("Private data")
2025-09-15T16:52:09.900+02:00 TRACE    [         -] Private data | _source=<stdin>.<module>:1
```

### Structured logging

Structured logging is not only about the output format.

For your logs to be well parsable by humans and machines, the event message and the event data must be separately recognizable.

Name the event in the message and keep the data separate:

```diff
- logger.info("Created user with username 'Daniel' and gid 1000.")
+ logger.info("Created user.", username="Daniel", gid=1000)
```

This will create logs that are readable for humans,
easy to analyse using CLI tools (`less`, `vi`, `grep`, `wc`),
and are directly digestible by log aggregators like [Logstash](https://www.elastic.co/logstash) and [Graylog](https://graylog.org).

```diff
- 2025-09-15T16:55:51.111+02:00 INFO     [         -] Created user with username 'Daniel' and gid 1000. | _source=<stdin>.<module>:1
+ 2025-09-15T16:55:51.111+02:00 INFO     [         -] Created user. | username=Daniel gid=1000 _source=<stdin>.<module>:1
```

#### Bind values

Structured logging libraries offer a convenient way to assemble the data to log, by contextualizing the logger instance.

```python
# somewhere_else.py

from lancelog import logger

logger = logger.bind(ip="192.168.0.1", user="some one")

def hello():
    logger.info("Hello world.", dimensions=4)

hello()
````

```text
2025-09-15T16:30:42.500+02:00 INFO     [         -] Hello world. | ip=192.168.0.1 user="some one" dimensions=4 _source=somewhere_else.hello:6
```

#### Catch errors

`catch()` can be used as decorator or as context manager:

```python
# catch.py

@logger.catch
def div_zero(x):
    100 / x

div_zero(0)
```

```text
2025-09-15T16:34:04.726+02:00 ERROR    [         -] An error has been caught in function '<module>', process 'MainProcess' (1576260), thread 'MainThread' (135186368079680): | _source=catch.<module>:7
Traceback (most recent call last):

> File "/home/dtroeder/git/dev/libraries/lancelog/catch.py", line 7, in <module>
    div_zero(0)
    └ <function div_zero at 0x7af385dafa60>

  File "/home/dtroeder/git/dev/libraries/lancelog/catch.py", line 5, in div_zero
    100 / x
          └ 0

ZeroDivisionError: division by zero
```

Using the `catch()` context manager reduces boilerplate code for common situations.

```python
# catch.py
filename = "/etc/shadow"
logger = logger.bind(filename=filename)
```

Without `catch()`:

```python
try:
    cfg = open(filename, "r").read()
except EnvironmentError:
    logger.exception(f"Reading configuration file.")
    raise
```

With `catch()`:

```python
with logger.catch(message="Reading configuration file.", level="ERROR", reraise=True):
    cfg = open(filename, "r").read()
```

```text
2025-09-15T16:36:30.884+02:00 ERROR    [         -] Reading configuration file. | _source=catch.<module>:5
Traceback (most recent call last):

> File "/home/dtroeder/git/dev/libraries/lancelog/catch.py", line 6, in <module>
    cfg = open(filename, "r").read()
               └ '/etc/shadow'

PermissionError: [Errno 13] Permission denied: '/etc/shadow'
Traceback (most recent call last):
  File "/home/dtroeder/git/dev/libraries/lancelog/catch.py", line 6, in <module>
    cfg = open(filename, "r").read()
          ^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 13] Permission denied: '/etc/shadow'
```

#### Contextualize

`contextualize()` is a context manager that reduces the need to create new loggers for select pieces of
code and uses contextvars for contexts unique to each thread and asynchronous task:

```python
# main.py

logger.info("Before...")

with logger.contextualize(task=task_id):
    # ...
    logger.info("Working on task.")

logger.info("After.")
```

```text
2025-09-15T16:38:10.790+02:00 INFO     [         -] Before... | _source=main.<module>:4
2025-09-15T16:38:10.790+02:00 INFO     [         -] Working on task. | task=24589179594642 _source=main.<module>:8
2025-09-15T16:38:10.790+02:00 INFO     [         -] After. | _source=main.<module>:10
```

#### Tracebacks

When calling `logger.exception()` in an exception handler,
traceback are both printed as multi-line text and added to data in field `_traceback`.

### uvicorn and FastAPI

`uvicorn.run()` applies uvicorn's default `LOGGING_CONFIG` when the server starts,
after `setup_logging()` has run.
That config gives the `uvicorn` and `uvicorn.access` loggers their own handlers
and sets `propagate: False`,
so their records bypass Loguru.

`uvicorn_log_config()` returns a replacement config.
It requires that `setup_logging()` has run first, otherwise it raises a `RuntimeError`.
The `uvicorn` logger then propagates to Loguru.
The `uvicorn.access` logger gets a handler that writes structured records.

```python
# main.py

import uvicorn
from fastapi import FastAPI

from lancelog import setup_logging, uvicorn_log_config

setup_logging()

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/users")
def users() -> list[str]:
    return []


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=uvicorn_log_config(health_paths=["/health"]),
    )
```

A request to `/users` is logged like this:

```text
2026-10-22T12:28:57.987+02:00 INFO     [         -] HTTP request. | client.address=127.0.0.1 client.port=45994 http.request.method=GET url.path=/users network.protocol.version=1.1 http.response.status_code=200 _source=h11_impl.send:477
```

Access log records (`uvicorn.access` logger) become structured records
with the message `HTTP request.` and these fields:

- `client.address`: `str`, omitted if uvicorn reports no client
- `client.port`: `int`, omitted if the client address has no port
- `http.request.method`: `str`
- `url.path`: `str`, without the query string
- `network.protocol.version`: `str`, e.g. `1.1`
- `http.response.status_code`: `int`

#### Log levels

- Access records are logged at `INFO`.
- Successful requests (status below 300) to the paths in `health_paths`
are logged at `DEBUG`
to keep Kubernetes probes out of the `INFO` output.
- Responses with status 500 or above are logged at `ERROR`, on every path.
- A health check that returns any other status (300 to 499) stays at `INFO`.

The `uvicorn` loggers stay at `INFO`, whatever level `setup_logging()` uses,
to keep uvicorn's per-connection chatter out.
To lower it, pass `log_level` to `uvicorn.run()`, for example `log_level="debug"`.
`setup_logging(level=...)` must use the same level.

#### `health_paths`

`health_paths` is compared with the path as uvicorn logs it:
percent-encoded and, since uvicorn 0.26, including `root_path`.
Behind a prefix (`uvicorn.run(..., root_path="/api/v1")`)
use `health_paths=["/api/v1/health"]`, not `["/health"]`.

#### Query string

The query string can contain secrets, so it is not part of the access record.
It is logged in a separate record at level `TRACE`.
That record reaches the sink only when Lancelog itself runs at `TRACE`:
`setup_logging(level="TRACE")`.

Request headers can contain credentials, such as the `Authorization` header.
They are not logged by default.
`log_request_headers` adds them to the `TRACE` record,
which is then written for every request,
with the message `HTTP request headers and query string.`:

- `log_request_headers=True`: all headers in one JSON field, `http.request.headers`.
- `log_request_headers=["user-agent", "x-forwarded-for"]`: one field per listed header,
  for example `http.request.header.user-agent`.

If uvicorn changes in a way that hides the headers from Lancelog,
the record carries the field `_request_headers_error` with the reason.

If uvicorn changes the access record,
Lancelog logs the record as text at its own level,
without the query string,
with the field `_access_record_error`,
and logs one `WARNING` per process that names the cause.
Failures inside the handler do not fail the request being logged.

The uvicorn command line (`--log-config`) is _not_ covered.
Start the server with `uvicorn.run()` as shown above.

### OpenTelemetry trace and span IDs

Install the extra, which pulls in the OpenTelemetry packages (version 1.42 or later, below 2):

```shell
pip install 'lancelog[otel]' \
  --index-url https://git.knut.univention.de/api/v4/projects/1449/packages/pypi/simple
```

Unless you enable an OpenTelemetry option, Lancelog never imports an `opentelemetry` module.

Set `DataSectionOptions(trace_context=True)` to add the IDs of the active span to the data section
(the `extra` field in JSON output) of every log record:

| Field         | Value                                                                  |
| ------------- | ---------------------------------------------------------------------- |
| `trace_id`    | 32 lowercase hexadecimal characters                                    |
| `span_id`     | 16 lowercase hexadecimal characters                                    |
| `trace_flags` | 2 lowercase hexadecimal characters, for example `03` for a sampled span |

The names follow the OpenTelemetry specification "Trace Context in non-OTLP Log Formats".
When no valid span is active, the three fields are absent.
Lancelog reads the span in the thread that logs, so the IDs are right even with `enqueue=True`.

```python
>>> from opentelemetry import trace
>>> from opentelemetry.sdk.trace import TracerProvider
>>> from lancelog import DataSectionOptions, setup_logging, logger
>>> trace.set_tracer_provider(TracerProvider())
>>> tracer = trace.get_tracer("example")
>>> setup_logging(data_options=DataSectionOptions(trace_context=True))
>>> with tracer.start_as_current_span("work"):
...     logger.info("Hello")
2026-10-20T10:00:00.000+02:00 INFO     [         -] Hello | trace_id=0af7651916cd43dd8448eb211c80319c span_id=b7ad6b7169203331 trace_flags=03 _source=<stdin>.<module>:2
```

The flags value depends on the SDK version and the sampler.
Recent SDK versions also set the "random trace ID" bit, so a sampled span has `03`.

### Forwarding logs to OpenTelemetry (OTLP)

Pass `OtelOptions` to `setup_logging()` to export log records to an OpenTelemetry endpoint over OTLP/HTTP (protobuf).
The export is an additional sink next to the one configured by `sink` and `level`.
It has its own minimum level.
`setup_logging(filter=...)` applies only to the stdout or file sink.
The OTLP sink filters by its own `level` and excludes the `opentelemetry*` loggers.
Both features are independent:
export works without `trace_context`, and `trace_context` works without export.

```python
from lancelog import DataFormat, OtelOptions, setup_logging

setup_logging(
    data_format=DataFormat.JSON,
    level="INFO",  # stdout
    otel=OtelOptions(
        level="DEBUG",  # OTLP
        endpoint="http://victorialogs:9428/insert/opentelemetry/v1/logs",
        service_name="my-service",
        resource_attributes={"deployment.environment": "staging"},
    ),
)
```

| `OtelOptions` field   | Default                      | Falls back to, when `None`                                   |
| --------------------- | ---------------------------- | ------------------------------------------------------------ |
| `level`               | `LogLevel.INFO`              | (no fallback) minimum level to export, independent of the stdout `level` |
| `endpoint`            | `None`                       | `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`, then `OTEL_EXPORTER_OTLP_ENDPOINT` (with `/v1/logs` appended), then `http://localhost:4318/v1/logs`. An explicit `endpoint` is used verbatim, so include the path (for example `/v1/logs`) |
| `headers`             | `None`                       | `OTEL_EXPORTER_OTLP_LOGS_HEADERS`, then `OTEL_EXPORTER_OTLP_HEADERS` |
| `service_name`        | `None`                       | `OTEL_SERVICE_NAME`, then the SDK default `unknown_service`  |
| `resource_attributes` | `None`                       | Merged over `OTEL_RESOURCE_ATTRIBUTES`                       |
| `timeout`             | `None`                       | `OTEL_EXPORTER_OTLP_LOGS_TIMEOUT`, then `OTEL_EXPORTER_OTLP_TIMEOUT`, then 10 seconds |

`headers` is left out of `repr()`, so authentication tokens do not appear in the debug record "Configured logging.".
An invalid `level` raises `ValueError` from `setup_logging()` before it changes the current configuration.

#### Exported record

| OpenTelemetry field | Source                                                          |
| ------------------- | --------------------------------------------------------------- |
| body                | The message of the log record, without the data section         |
| timestamp           | Time of the log record                                          |
| severity            | See the table below; the Loguru level name is the severity text |
| attributes          | The `extra` fields, see below                                   |
| `code.file.path`, `code.function.name`, `code.line.number` | Source of the log call |
| `exception.type`, `exception.message`, `exception.stacktrace` | The exception, if one is attached (for example by `logger.exception()`) |
| trace context       | See below                                                       |

| Loguru level | OpenTelemetry severity |
| ------------ | ---------------------- |
| `TRACE`      | `TRACE`                |
| `DEBUG`      | `DEBUG`                |
| `INFO`       | `INFO`                 |
| `SUCCESS`    | `INFO2`                |
| `WARNING`    | `WARN`                 |
| `ERROR`      | `ERROR`                |
| `CRITICAL`   | `FATAL`                |

Custom levels map by their number: below 10 `TRACE`, below 20 `DEBUG`, below 30 `INFO`, below 40 `WARN`, below 50 `ERROR`, otherwise `FATAL`.

Attributes come from the `extra` fields, for example from `logger.bind()` and `logger.contextualize()`.
OpenTelemetry attributes accept only primitives and homogeneous sequences of primitives.
Lancelog keeps `str`, `bool`, `int` and `float` values and lists or tuples whose items are all of the same primitive type.
It converts every other value with `str()`.
Fields with the value `None` are dropped.
`request_id` is exported as an attribute: always with `DataFormat.JSON`, otherwise only with `DataSectionOptions(request_id=True)`.
The internal fields `serialized` and `_traceback` and the source reference field (`_source` by default) are not exported as attributes.
The fields `trace_id`, `span_id` and `trace_flags` become the trace context of the record when they hold valid hexadecimal IDs,
and are exported as attributes otherwise.

Trace context: the exported record carries the trace and span ID of the span that is active when the log call happens,
whether or not `trace_context` is set.
With `DataSectionOptions(trace_context=True)` the IDs also appear in the stdout or file output,
and the export uses the IDs bound to the record.

Log records of the loggers `opentelemetry*` are never exported.
That includes the SDK's own error messages, which Lancelog receives through the redirected `logging` module.
Records emitted while the SDK exports (in its export thread) are skipped too.
Both exclusions prevent a feedback loop when the endpoint is unreachable.

#### Provider and lifecycle

Lancelog creates a private `LoggerProvider`.
It does not set it as the global provider and does not reuse a global one.
Calling `setup_logging()` again flushes and shuts down the previous provider before it creates a new one.
An unreachable endpoint neither raises into the application nor blocks log calls.
Records are exported in batches by a worker thread, which sends a batch after 1 second by default (`OTEL_BLRP_SCHEDULE_DELAY`).

Pending records are flushed through an `atexit` hook when the interpreter exits normally.
With an unreachable endpoint, this flush can delay the exit by up to the export timeout (`OtelOptions.timeout`, 10 seconds by default) per pending batch of 512 records, and by at most the SDK's 30 second shutdown timeout.
The same applies when `setup_logging()` is called again.
They are lost on `SIGKILL`.
In a plain script, an unhandled `SIGTERM` terminates the process without running `atexit` hooks, so install a signal handler that raises `SystemExit`.
Uvicorn handles `SIGTERM` and exits normally.

#### Debian and UCS

The `otel` extra applies to installations with `pip` or `uv`.
OpenTelemetry Python is not packaged for UCS 5.2 or for Debian bookworm and trixie (it is in Debian forky and sid).
The Debian package `python3-lancelog` does not depend on it,
and without the OpenTelemetry packages `setup_logging()` works as before.
Enabling `trace_context` or passing `otel` raises an `ImportError` that names the extra.

#### Example

[`docs/otel-example/`](docs/otel-example/README.md) contains a FastAPI application with OpenTelemetry tracing
and a VictoriaLogs container that receives and displays its logs.

## Listener and UMC modules, UDM extensions (univention-debug)

The Listener modules, UMC modules, UDM, and UDM extensions use "univention-debug" (UD) to write logs.
UD is implemented in C with Python bindings.
It has very limited configurability, unusual log levels, and a cumbersome API.
It doesn't support configurable sinks or handlers like Python logging and Loguru,
so its output can't be redirected into them.

Lancelog could be made to work in Listener modules, UMC modules, UDM, and UDM extensions,
by sending its output to UD instead of STDOUT.

But I have decided against implementing this feature.
UD is deprecated, and supporting it will only increase Lancelog's complexity without a strong need,
because there is an alternative.

If you wish to work on those module, please use univention-logging (UL):

```python
import logging

logger = logging.getLogger("ADMIN").getChild(__name__)  # set the UD category "ADMIN", get a local logger instance

logger.info("test")
```

Don't forget to configure UL at the main entrypoint of your application
(doesn't apply if you are working on a plugin like a Listener module, UMC module, or UDM extension):

```python
import univention.logging

univention.logging.basicConfig(level=logging.INFO)
```

## Background

Univention has a comprehensive concept for logging and multiple ADRs that define how to implement it:

- [Concept](https://git.knut.univention.de/univention/internal/research-library/-/blob/main/research/logging_concept/README.md)
- [ADR 0004 Logging Topology](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0004-logging-topology.md)
- [ADR 0005 Log levels](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0005-log-levels.md)
- [ADR 0010 Log format](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0010-log-messages.md) (supersedes [ADR 0006 Log format](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0006-log-format.md))
- [ADR 0007 Log messages](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0007-log-messages.md)
- [ADR 0008 Structured logging](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0008-structured-logging.md)

This library strives to implement all concepts from the ADRs, and support developers when implementing them.

## Best practices

Please read [ADR 0005, section "Log levels"](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0005-log-levels.md#log-levels-1)
for a summary how to use log levels.

Read [ADR 0007 section "Log messages"](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0007-log-messages.md#log-messages-1)
for more details and examples regarding message content, addressee and level.

Read [ADR 0007 section "Logging errors"](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0007-log-messages.md#logging-errors)
for instructions how to log errors.

## Sensitive data

Sensitive data, including technical secrets like passwords and tokens,
and personal information like username, real name, password, email, postal address,
MUST only be logged at level `TRACE`.

## Development

This project uses [pre-commit](https://pre-commit.com/) for managing and maintaining pre-commit hooks.

This project uses [uv](https://docs.astral.sh/uv/) as Python package and project manager.

See their respective web pages for installation and usage instructions.

### Tests

```shell
uv sync --all-extras

uv run pytest
```

The test run fails below 100% coverage (`--cov-fail-under=100`).
