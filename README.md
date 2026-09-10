# minimal-request

Finds the smallest version of an HTTP request that still produces the same
response. Takes a curl command, HAR file or Bruno `.bru` file, replays it while
removing parts, and prints what is left.

## Install

```console
uv tool install minimal-request                  # the minreq command
uv tool install 'minimal-request[impersonate]'   # adds TLS fingerprinting
```

## Usage

Everything after `--` is parsed as a curl command; the result goes to stdout and
the report to stderr. Files and stdin work too: `minreq request.bru -f httpie`,
`cat command.txt | minreq -`.

```console
$ minreq -- curl 'https://api.example.com/v1/me?verbose=1' \
    -H 'authorization: Bearer eyJhbG...' \
    -b 'sid=9f2a; theme=dark; _ga=GA1.2.99'
  baseline  200 application/json 1483B sha256:6b86b2...
  minimised 41 requests · 18 atoms -> 2 · 1194 bytes saved

curl 'https://api.example.com/v1/me' -H 'authorization: Bearer eyJhbG...' -b 'sid=9f2a'
```

## How it works

1. The request is sent once and the response recorded as a signature
   `(status, content-type, body size, body sha256)`.
2. It is split into *atoms*: each header, each element of a comma-separated
   header (`Accept: a, b, c` gives three), each cookie, each query parameter,
   and each form field or top-level JSON key.
3. Delta debugging removes subsets of atoms and replays, keeping any removal
   that leaves the signature unchanged.
4. The survivors are rendered in the requested output formats.

The algorithm is ddmin (Zeller & Hildebrandt) with partitions balanced by each
atom's serialised byte length rather than count, so large atoms are isolated in
earlier rounds; a final heaviest-first pass makes the result 1-minimal. It
sends the request tens to hundreds of times, so use it only on idempotent
requests.

## HAR archives

With a HAR file and no `--format` override, every `GET` entry is minimised in
place and the rest of the archive copied unchanged.

```console
minreq -o minimal.har session.har
minreq session.har              # writes session-minimal.har, then -1, -2, ...
```

## Options

| Option | |
|---|---|
| `-f, --format` | `curl`, `har`, `bru`, `httpie`, `requests`, `httpx`, `fetch`, `wget`, `powershell`, `http`, `json`; comma-separated |
| `-o, --output` | write to a file instead of stdout |
| `--impersonate ID` | replay via [curl_cffi](https://github.com/lexiforest/curl_cffi) with a browser TLS fingerprint |
| `--match` | signature fields that must hold: `status,type,hash` (default), or `size` when a body embeds a token or timestamp and its sha256 never repeats |
| `--baseline-runs N` | baseline replays used to detect unstable responses (default 2) |
| `--keep PATTERN` | never remove matching atoms, e.g. `--keep 'header:authorization'` |
| `--entry N\|TEXT` | restrict a HAR archive to one entry |
| `--dry-run` | list atoms and exit without sending anything |

An unstable baseline is detected and `--match` relaxed automatically. Run
`minreq --help` for the remaining flags.

## Library

Requests cross the API boundary as typed [httpx2](https://github.com/pydantic/httpx2)
objects; Python has no equivalent of PHP's PSR-7. `minimise()` also accepts a
curl command string and takes the same options as the CLI, `impersonate` included.

```python
import httpx2
from minreq import minimise

result = minimise(httpx2.Request("GET", url, headers={"x-a": "1"}), impersonate="chrome131")
result.as_httpx2()   # httpx2.Request, minimised
result.to_curl()     # command string; also result.kept / .removed / .stats
```

## Notes

Chrome and Firefox `Copy as cURL` output parses as-is, including `--compressed`,
`--data-raw` and `$'...'` strings. Redirects are not followed unless the command
passes `-L`. Requests are sent with exactly the headers given, plus `Host` and
`Content-Length`. Prior art: [minreq](https://pypi.org/project/minreq/) (2015,
Python 2) applied the same idea to Copy as cURL, testing each field once against
the full request, reporting individually-required fields, not a minimal set.

## License

MIT
