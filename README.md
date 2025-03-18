# ♞ Lancelog ♞ - Setup logging like a hero 🌟

A library that helps developers to set up logging in their applications in an easy and consistent way.

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

By default, _lancelog_ will write in [Logfmt](https://brandur.org/logfmt) format.
If you wish to write JSON instead, install:

```shell
pip install lancelog[json] \
  --index-url https://git.knut.univention.de/api/v4/projects/1449/packages/pypi/simple
```

This will pull in the [orjson](https://github.com/ijl/orjson) library.

### Usage in pyproject.toml

To reference _lancelog_ in your projects `pyproject.toml`, add to it:

```toml
[[tool.poetry.source]]
name = "lancelog"
url = "https://git.knut.univention.de/api/v4/projects/1449/packages/pypi/simple"
priority = "supplemental"
```

## Usage

1. Call `setup_logging()` in your application's startup routine.
2. Import the Loguru logger in each module: `from loguru import logger`
3. Use the logger: `logger.info("Hello")`

Calls to the Python logging libraries are intercepted and redirected into Loguru.
But, while it is possible to use Python logging, it is discouraged, because it doesn't provide structured logging
(see section [Structured logging](#Structured%20logging)).

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
) -> None:
    """
    Configure logging.

    :param sink: A "file-like object" like `sys.stdout` (default), `sys.stderr`,
        an opened file, or a file path as `str` or `pathlib.Path`.
    :param fmt: The template used to format logged messages. Does not apply for JSON output.
        Default is `lancelog.setup.LOG_FORMAT_DEFAULT`.
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
    :return: None
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

### Structured logging

Structured logging is not only the output format.

For your logs to be well parsable by humans and machines, the event and the data must be separately recognizable.

Name the event in the message and keep the data separate:

```diff
- logger.info("Created user with username 'Daniel' and gid 1000.")
+ logger.info("Created user.", username="Daniel", gid=1000)
```

This will create logs that are readable for humans,
easy to analyse using CLI tools (`less`, `vi`, `grep`, `wc`),
and are directly digestible by log aggregators like [Logstash](https://www.elastic.co/logstash) and [Graylog](https://graylog.org).

#### Bind values

Structured logging libraries offer a convenient way to assemble the data to log, by contextualizing the logger instance.

```python
# somewhere_else.py

from loguru import logger

logger = logger.bind(ip="192.168.0.1", user="some one")

logger.info("Hello world.", dimensions=4)
```

```text
2025-03-17 10:23:56.821 +0100 |    INFO | somewhere_else.<module>:5 | Hello world. | ip=192.168.0.1 user="some one" dimensions=4
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
2025-03-17 10:32:23.989 +0100 |   ERROR | catch.<module>:14 | An error has been caught in function '<module>', process 'MainProcess' (2538020), thread 'MainThread' (135891779491648): |
Traceback (most recent call last):

> File "catch.py", line 14, in <module>
    div_zero(0)
    └ <function div_zero at 0x7b97c48289a0>

  File "catch.py", line 12, in div_zero
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
2025-03-17 10:36:27.654 +0100 |   ERROR | catch.<module>:19 | Reading configuration file. | filename=/etc/shadow
Traceback (most recent call last):

> File "catch.py", line 20, in <module>
    cfg = open(filename, "r").read()
               └ '/etc/shadow'

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
2025-03-17 10:41:08.064 +0100 |    INFO | main.<module>:24 | Before... | ip=192.168.0.1 user="some one"
2025-03-17 10:41:08.064 +0100 |    INFO | main.<module>:28 | Working on task. | task=24589179594642 ip=192.168.0.1 user="some one"
2025-03-17 10:41:08.064 +0100 |    INFO | main.<module>:30 | After. | ip=192.168.0.1 user="some one"
```

## Background

Univention has a comprehensive concept for logging and multiple ADRs that define how to implement it:

- [Concept](https://git.knut.univention.de/univention/internal/research-library/-/blob/main/research/logging_concept/README.md)
- [ADR 0004 Logging Topology](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0004-logging-topology.md)
- [ADR 0005 Log levels](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0005-log-levels.md)
- [ADR 0006 Log format](https://git.knut.univention.de/univention/decision-records/-/blob/main/dev/0006-log-format.md)
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

## Move level from DEBUG to TRACE

The logs of the `http.client` and `urllib3` libraries at `DEBUG` level contain sensitive information.
So, we're lowering all `DEBUG` level output to `TRACE`.

The mechanism is extensible.
If you wish to move the `DEBUG` output of other loggers to `TRACE`:

```python
from lancelog import LOG_LEVEL_DEBUG_TO_TRACE

LOG_LEVEL_DEBUG_TO_TRACE.add("logger_name")
```

## Development

This project uses [pre-commit](https://pre-commit.com/) for managing and maintaining pre-commit hooks.

This project uses [uv](https://docs.astral.sh/uv/) as Python package and project manager.

See their respective web pages for installation and usage instructions.

### Tests

**TODO**: Write tests!
