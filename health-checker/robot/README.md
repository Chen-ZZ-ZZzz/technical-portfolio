# Robot Framework tests for the journal scanner

| Suite | Checks | Needs |
|---|---|---|
| `scanner_units.robot` (tag `live`) | timer active, ran within the last hour, last run succeeded | the deployed `checker-journal-scan` user units |
| `scanner_script.robot` | bad inputs to `journal_scan.sh`: invalid `SCAN_DELAY`, garbage cursor, extra argument | bash, jq, python3, systemd |

## Usage

```bash
uv sync
uv run robot -d results/ scanner_units.robot scanner_script.robot
uv run robot -d results/ --exclude live scanner_units.robot scanner_script.robot
```
## Design

`rc` is checked two ways, `${1}` in the script suite, and `type=int` in the units suite, both strict. Each suite sticks to one form.

`systemctl show` exits 0 even if a unit does not exist at all, and returns mostly default values. `Result` reads `success`. So a unit's `LoadState` property is tested first.

To safety guard the live state files and live production directories, `STATE_DIRECTORY` is directed to temporary directory, the script is run as a copy there too.

`LC_ALL=C` sets outputs to English regardless language used by local operating system.
