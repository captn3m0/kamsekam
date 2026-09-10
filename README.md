# kamsekam

Finds the smallest version of an HTTP request that still produces the same
response. Takes a request, replays it while removing parts, and prints what is left.

## Install

```console
uv tool install kamsekam                # the kamsekam command
uv tool install 'kamsekam[impersonate]' # adds browser impersonation using curl_cffi
```

## Usage

Everything after `--` is parsed as a curl command:

```console
$ kamsekam -- curl 'https://data.services.jetbrains.com/products?code=TC&release.type=eap%2Crc%2Crelease&fields=distributions%2Clink%2Cname%2Creleases&_=1789067502753' \
  --compressed \
  -H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:155.0) Gecko/20100101 Firefox/155.0' \
  -H 'Accept: */*' \
  -H 'Accept-Language: en' \
  -H 'Accept-Encoding: gzip, deflate, br, zstd' \
  -H 'Origin: https://www.jetbrains.com' \
  -H 'DNT: 1' \
  -H 'Sec-GPC: 1' \
  -H 'Connection: keep-alive' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-site' \
  -H 'Priority: u=4' \
  -H 'TE: trailers'
  input     GET https://data.services.jetbrains.com/products · 20 atoms · 503 bytes
  baseline  200 application/json 176949B sha256:67308c6c7677
  minimised 21 requests · 20 atoms → 2 · 388 bytes saved

    keep  query:code                   ?code=TC
    keep  query:fields                 ?fields=distributions%2Clink%2Cname%2Creleases
    drop  18 removed: header:user-agent, header:accept, header:accept-language, header:accept-encoding[0], header:accept-encoding[1], header:accept-encoding[2], header:accept-encoding[3], header:origin, header:dnt, header:sec-gpc, header:connection, header:sec-fetch-dest, header:sec-fetch-mode, header:sec-fetch-site, header:priority, header:te, query:release.type, query:_

curl 'https://data.services.jetbrains.com/products?code=TC&fields=distributions%2Clink%2Cname%2Creleases'
```

## How it works

1. The request is sent once and the response recorded as a signature
   `(status, content-type, body size, body sha256)`.
2. It is split into *atoms*: each header, each element of a comma-separated
   header (`Accept: a, b, c` gives three), each cookie, each query parameter,
   and each form field or top-level JSON key.
3. Delta debugging removes subsets of atoms and replays, keeping any removal
   that leaves the signature unchanged, then renders what survived.

The algorithm is ddmin (Zeller & Hildebrandt) [^1]. It sends
the request hundreds of times, so use it only on idempotent requests.

[^1]: https://doi.org/10.1109/32.988498 "Simplifying and Isolating Failure-Inducing Input", *IEEE Transactions on Software Engineering*"

## HAR archives

With a HAR file and no `--format` override, every `GET` entry is minimised in
place, the rest of the archive copied unchanged.

```console
kamsekam -o minimal.har session.har
kamsekam session.har              # writes session-minimal.har, then -1, -2, ...
```

## Options

| Option | |
|---|---|
| `-f, --format` | `curl`, `har`, `httpie`, `http`, `json`; comma-separated |
| `-o, --output` | write to a file instead of stdout |
| `--impersonate ID` | replay via [curl_cffi](https://github.com/lexiforest/curl_cffi) with a browser TLS fingerprint |
| `--match` | signature fields that must hold: `status,type,hash` (default), or `size` when a body embeds a token or timestamp and its sha256 never repeats |
| `--baseline-runs N` | baseline replays used to detect unstable responses (default 2) |
| `--keep PATTERN` | never remove matching atoms, e.g. `--keep 'header:authorization'` |
| `--dry-run` | list atoms and exit without sending anything |

An unstable baseline is detected and `--match` relaxed automatically. Run
`kamsekam --help` for the rest.

## Library

Minimized requests are returned as `httpx2.Request` objects, but are also available
as curl strings.

```python
import httpx2
from kamsekam import minimise, to_curl, uncurl

uncurl("curl https://e.test -H 'a: b' --compressed")   # -> httpx2.Request; to_curl() reverses it

result = minimise(httpx2.Request("GET", url, headers={"x-a": "1"}), impersonate="chrome131")
result.as_httpx2()   # httpx2.Request, minimised
result.to_curl()     # command string; also result.kept / .removed / .stats
```

## License

Licensed under Unlicense
