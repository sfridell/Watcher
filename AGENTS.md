# AGENTS.md

## Project Overview

Watcher is a Python CLI tool for monitoring and managing home network routers (specifically DD-WRT based routers) via SSH.

## Commands

### Running the Tool
```bash
python watcher.py <command> [subcommands] [options]
```

### Testing
No automated tests currently exist. Manual testing by running commands against a live router.

### Linting/Type Checking
```bash
ruff check .
```

## Code Conventions

- Python 3.x
- Use `argparse` for CLI argument parsing
- Use `fabric` for SSH connections
- Store connection data in `connections.json`
- Store RSA keys in `./keyfiles/` directory
- Functions that handle CLI commands accept `(args, connections, output)` parameters where `output` is an `io.StringIO` stream
- Error handling: raise `Exception` with descriptive messages for remote command failures

## Project Structure

```
.
├── watcher.py        # Main CLI entry point and command handlers
├── connectiondb.py   # Connection management and SSH key handling
├── connections.json  # Stored connection profiles (gitignored recommended)
├── keyfiles/         # RSA key pairs for SSH auth
└── Dockerfiles/      # Docker configurations
```

## Key Dependencies

- `fabric` - SSH client library
- `cryptography` - RSA key generation
- `tabulate` - Table formatting for output

## Notes

- The tool expects DD-WRT style router commands (nvram, brctl, etc.)
- SSH connections use RSA keys with SHA1 compatibility (`disabled_algorithms` for rsa-sha2)
- When adding new commands, follow the existing subparser pattern in `get_args()`
