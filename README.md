# apksecrets

Checkpointed, local-only secret scanner for APK and split APK archives. Discovers apps from F-Droid / Aptoide / APKMirror (or imports local files), downloads them, unpacks, scans with [trufflehog](https://github.com/trufflesecurity/trufflehog), and keeps every finding in a local SQLite database for triage.

## How it works

```
discover → download → unpack → scan → retain
```

Every job is checkpointed in `state.sqlite3` (`--state` directory, default `.apksecrets/`). Interrupted scans resume; failed jobs retry and can be re-queued. APKs are deleted after scanning — secrets and the scan report are retained in SQLite, so nothing is re-scanned or re-downloaded and disk usage stays minimal.

## Install

Requires Python 3 with [`rich`](https://github.com/Textualize/rich) and a [trufflehog](https://github.com/trufflesecurity/trufflehog) binary on `PATH`.

```sh
pip install rich
brew install trufflehog        # or download from trufflehog releases
```

## Usage

```sh
# continuous keyword scan across all sources
./apksecrets run --keyword whatsapp --keyword signal --forever

# single exact package
./apksecrets run --package org.thoughtcrime.securesms

# import local APKs instead of scraping
./apksecrets import /path/to/apks

# web UI (start/stop scans, browse findings, mark working/broken)
./apksecrets serve --port 8000
```

### Sources

| source | notes |
| --- | --- |
| `fdroid` | full index scan; keywords match package id or app name |
| `aptoide` | search API; keyword scans queue every matching app |
| `apkcombo` | search page scrape; latest release of each matching app |
| `local` | via `import` subcommand or `--import` |

### Commands

| command | purpose |
| --- | --- |
| `run` | discovery + scanning loop (`--forever`, `--interval`, `--workers`, `--keyword`, `--package`, `--random`) |
| `serve` | web UI on `http://127.0.0.1:8000` |
| `import` | queue a local `.apk` / `.apkm` / `.apks` file or folder |
| `apps` / `secrets` / `status` / `failures` | inspect state (`--working 1` filters verified secrets) |
| `mark <id> <working\|broken>` | verdict on a secret |
| `retry-failed` | re-queue failed jobs |

## Web UI

- Overview: start/stop scans, live stats and terminal output (Escape stops too)
- Secrets: filter all/working/broken/unmarked with live counts, free-text search (`/` focuses it), sort by any column, click a value (or ⧉) to copy it, detector names link to where the secret is used, export filtered rows as CSV or JSON; an auto-verifier marks supported secret types (Slack, Telegram, Discord, GitHub, OpenAI, …) working/broken as they appear
- Apps: searchable, sortable table; click a package to jump to its secrets; retry failed
- Both tables fit their card and have drag-resizable columns (double-click a column edge to reset; widths persist in the browser)

Continuous (`forever`) scans only queue releases not already in the database, so every cycle keeps discovering new apps instead of rescanning old ones.

## Safety

- Downloads verified as ZIP/APK before replace; unpack guarded against archive bombs and path traversal.
- Web UI and database are local-only by default (`127.0.0.1`).
- Findings are stored on disk with `0600` permissions in the `--state` directory.

## Development

```sh
python3 -m unittest test_apksecrets -v
```

## License

[MIT](LICENSE)
