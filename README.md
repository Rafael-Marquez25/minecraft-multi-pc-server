# minecraft-multi-pc-server

Python CLI launcher for hosting one custom Minecraft server folder from multiple PCs without overwriting each other.

The first version is Windows-first for real hosting, but the core logic is testable from Ubuntu. It assumes your server folder is already synced by Google Drive Desktop and that your modpack provides a `.bat` or `.ps1` script that stays open until the Minecraft server stops.

## Quick start

1. Copy `config.example.toml` to `config.toml`.
2. Set `remote_server_dir` to the server folder inside Google Drive.
3. Set `local_server_dir` to a local working folder.
4. Set `start_command` to the modpack script command.
5. Run:

```bash
python3 -m minecraft_multi_pc_server -c config.toml run
```

## Commands

```bash
python3 -m minecraft_multi_pc_server -c config.toml run
python3 -m minecraft_multi_pc_server -c config.toml status
python3 -m minecraft_multi_pc_server -c config.toml sync-up
python3 -m minecraft_multi_pc_server -c config.toml unlock --force
```

`run` creates a best-effort lock in the shared state folder, copies the remote server folder to the local folder, starts your configured script, waits for it to exit, copies local changes back, and releases the lock.

If uploading fails after the server stops, the lock is kept with status `upload_failed`. Fix the problem and run `sync-up` from the same PC to retry the upload and release the lock.

To run the automated tests on Ubuntu:

```bash
python3 -m unittest discover -s tests -v
```

## Windows notes

On Windows, the launcher uses `robocopy /MIR` for folder mirroring. `robocopy` exit codes `0` through `7` are treated as success.

Google Drive folder locks are not perfectly atomic. Avoid launching from two PCs at exactly the same time before Drive has synced the lock file.
