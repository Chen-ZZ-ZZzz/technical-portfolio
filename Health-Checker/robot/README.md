# Robot Framework tests for the journal scanner

| Suite | Checks | Needs |
|---|---|---|
| `scanner_units.robot` (tag `live`) | timer active, ran within the last hour, last run succeeded | the deployed `checker-journal-scan` user units |
| `scanner_script.robot` | bad inputs to `journal_scan.sh`: invalid `SCAN_DELAY`, garbage cursor, extra argument | bash, jq, python3, systemd |
| `scanner_units_ssh.robot` (tag `live`) | SSH version of `scanner_units.robot` | the deployed units, sshd, a key login to localhost (see [SSH setup](#ssh-setup)) |

## Usage

```bash
uv sync
uv run robot -d results/ scanner_units.robot scanner_script.robot scanner_units_ssh.robot
uv run robot -d results/ --exclude live scanner_units.robot scanner_script.robot scanner_units_ssh.robot
```

Defaults can be overridden with `-v NAME:value`:

| Variable | Suite | Default |
|---|---|---|
| `SCAN_WRAPPER` | `scanner_script.robot` | the repo copy, `../journal_scan.sh` |
| `HOST` | `scanner_units_ssh.robot` | `localhost` |
| `SSH_USER` | `scanner_units_ssh.robot` | `%{USER}`, the user running robot |
| `SSH_KEY` | `scanner_units_ssh.robot` | `%{HOME}/.ssh/robot_localhost`. Give an absolute path: `~` is not expanded |

## SSH setup

The SSH suite logs in with a key that has no passphrase, so it runs without a password prompt or an agent. Set it up once on the host:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/robot_localhost -N '' -C robot-localhost
ssh-copy-id -i ~/.ssh/robot_localhost.pub localhost
```

Then put `from="127.0.0.1,::1" ` in front of the key's line in `~/.ssh/authorized_keys`, so the unlocked key works only from this machine. Check the login the way the suite does it, with this key only and no agent:

```bash
ssh -i ~/.ssh/robot_localhost -o IdentitiesOnly=yes -o IdentityAgent=none localhost true; echo $?
```

SSHLibrary accepts any host key on first connect (paramiko's `AutoAddPolicy`) and never reads `known_hosts`. That is fine for localhost. Before pointing `HOST` at another machine, verify its fingerprint by hand.

## Design

`rc` is checked two ways, `${1}` in the script suite, and `type=int` in the two units suites, both strict. Each suite sticks to one form.

`systemctl show` exits 0 even if a unit does not exist at all, and returns mostly default values. `Result` reads `success`. So a unit's `LoadState` property is tested first.

To protect the live state files and production directories, `STATE_DIRECTORY` points to a temporary directory, and the script runs as a copy there.

`LC_ALL=C` forces English output regardless of the system locale.
