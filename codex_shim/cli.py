from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from .catalog import codex_config_overrides, write_catalog, write_config
from .settings import DEFAULT_FACTORY_SETTINGS, DEFAULT_HOST, DEFAULT_PORT, FactorySettings, default_model_slug


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".codex-shim"
CATALOG_PATH = RUNTIME_DIR / "custom_model_catalog.json"
CONFIG_PATH = RUNTIME_DIR / "config.toml"
PID_PATH = RUNTIME_DIR / "shim.pid"
LOG_PATH = RUNTIME_DIR / "shim.log"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
CODEX_CONFIG_BACKUP_PATH = RUNTIME_DIR / "config.toml.before-codex-shim"
MANAGED_BEGIN = "# >>> codex-shim managed >>>"
MANAGED_END = "# <<< codex-shim managed <<<"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-shim")
    parser.add_argument("--settings", type=Path, default=DEFAULT_FACTORY_SETTINGS)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate")
    sub.add_parser("list")
    sub.add_parser("start")
    sub.add_parser("enable")
    sub.add_parser("stop")
    sub.add_parser("disable")
    sub.add_parser("restart")
    sub.add_parser("status")
    sub.add_parser("patch-app", help="Patch Codex Desktop model dropdown to allow custom catalog models.")
    sub.add_parser("restore-app", help="Restore Codex Desktop app.asar from the pre-patch backup.")

    model_parser = sub.add_parser("model", help="List or set the active shim model in Codex config.")
    model_sub = model_parser.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("list")
    use_parser = model_sub.add_parser("use")
    use_parser.add_argument("model_slug")

    codex_parser = sub.add_parser("codex", help="Run Codex CLI with opt-in shim config overrides.")
    codex_parser.add_argument("args", nargs=argparse.REMAINDER)

    app_parser = sub.add_parser("app", help="Launch Codex Desktop with opt-in shim config overrides.")
    app_parser.add_argument("-m", "--model", dest="model_slug")
    app_parser.add_argument("path", nargs="?", default=".")

    args = parser.parse_args(argv)
    if args.command == "generate":
        generate(args.settings, args.port)
        return 0
    if args.command == "list":
        return list_models(args.settings)
    if args.command in {"start", "enable"}:
        generate(args.settings, args.port)
        code = start(args.settings, args.port)
        if code == 0 and args.command == "enable":
            install_codex_config(args.settings, args.port)
        return code
    if args.command in {"stop", "disable"}:
        if args.command == "disable":
            restore_codex_config()
        return stop()
    if args.command == "restart":
        stop()
        generate(args.settings, args.port)
        return start(args.settings, args.port)
    if args.command == "status":
        return status(args.port)
    if args.command == "patch-app":
        return patch_codex_app()
    if args.command == "restore-app":
        return restore_codex_app_bundle()
    if args.command == "model":
        if args.model_command == "list":
            return list_models(args.settings)
        if args.model_command == "use":
            generate(args.settings, args.port)
            ensure_started(args.settings, args.port)
            install_codex_config(args.settings, args.port, args.model_slug)
            print(f"Active Codex shim model: {args.model_slug}")
            return 0
    if args.command == "codex":
        generate(args.settings, args.port)
        ensure_started(args.settings, args.port)
        exec_codex(args.settings, args.port, args.args)
        return 0
    if args.command == "app":
        generate(args.settings, args.port)
        ensure_started(args.settings, args.port)
        install_codex_config(args.settings, args.port, args.model_slug)
        exec_codex_app(args.settings, args.port, args.path)
        return 0
    return 2


def generate(settings_path: Path, port: int) -> None:
    models = FactorySettings(settings_path).load()
    write_catalog(models, CATALOG_PATH)
    write_config(models, CONFIG_PATH, CATALOG_PATH, port)
    print(f"Generated {len(models)} model entries:")
    print(f"  catalog: {CATALOG_PATH}")
    print(f"  config:  {CONFIG_PATH}")
    print("No files under ~/.codex were modified.")


def install_codex_config(settings_path: Path, port: int, model_slug: str | None = None) -> None:
    models = FactorySettings(settings_path).load()
    default_slug = _resolve_model_slug(models, model_slug)
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    original = CODEX_CONFIG_PATH.read_text() if CODEX_CONFIG_PATH.exists() else ""
    if MANAGED_BEGIN not in original and not CODEX_CONFIG_BACKUP_PATH.exists():
        CODEX_CONFIG_BACKUP_PATH.write_text(original)
    cleaned = _remove_managed_config(original)
    cleaned = _remove_top_level_keys(cleaned, {"model", "model_provider", "model_catalog_json"})
    cleaned = _remove_section(cleaned, "model_providers.factory_byok_shim")
    top_block, provider_block = _managed_config_blocks(default_slug, port)
    CODEX_CONFIG_PATH.write_text(top_block + "\n" + cleaned.lstrip() + "\n" + provider_block)
    print(f"Installed shim config into {CODEX_CONFIG_PATH}.")
    print(f"Original backup: {CODEX_CONFIG_BACKUP_PATH}")


def list_models(settings_path: Path) -> int:
    models = FactorySettings(settings_path).load()
    width = max([len(m.slug) for m in models] + [4])
    for model in models:
        print(f"{model.slug:<{width}}  {model.display_name}  ->  {model.model} ({model.provider})")
    return 0


def start(settings_path: Path, port: int) -> int:
    if _pid_running(_read_pid()):
        print(f"Shim already running with pid {_read_pid()}.")
        return 0
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_PATH.open("ab")
    cmd = [
        sys.executable,
        "-m",
        "codex_shim.server",
        "--settings",
        str(settings_path),
        "--host",
        DEFAULT_HOST,
        "--port",
        str(port),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env, stdout=log, stderr=log, start_new_session=True)
    PID_PATH.write_text(str(process.pid))
    for _ in range(50):
        if _healthy(port):
            print(f"Shim started on http://{DEFAULT_HOST}:{port} with pid {process.pid}.")
            print(f"Log: {LOG_PATH}")
            return 0
        if process.poll() is not None:
            print(f"Shim exited during startup. See {LOG_PATH}.", file=sys.stderr)
            return 1
        time.sleep(0.1)
    print(f"Shim process started but health check timed out. See {LOG_PATH}.", file=sys.stderr)
    return 1


def stop() -> int:
    pid = _read_pid()
    if not _pid_running(pid):
        print("Shim is not running.")
        PID_PATH.unlink(missing_ok=True)
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not _pid_running(pid):
            PID_PATH.unlink(missing_ok=True)
            print("Shim stopped.")
            return 0
        time.sleep(0.1)
    print(f"Shim pid {pid} did not exit after SIGTERM.", file=sys.stderr)
    return 1


def restore_codex_config() -> None:
    if CODEX_CONFIG_BACKUP_PATH.exists():
        CODEX_CONFIG_PATH.write_text(CODEX_CONFIG_BACKUP_PATH.read_text())
        CODEX_CONFIG_BACKUP_PATH.unlink()
        print(f"Restored original {CODEX_CONFIG_PATH}.")
        return
    if CODEX_CONFIG_PATH.exists():
        current = CODEX_CONFIG_PATH.read_text()
        restored = _remove_managed_config(current)
        restored = _remove_section(restored, "model_providers.factory_byok_shim")
        CODEX_CONFIG_PATH.write_text(restored.lstrip())
        print(f"Removed shim config from {CODEX_CONFIG_PATH}.")


def status(port: int) -> int:
    pid = _read_pid()
    if _pid_running(pid) and _healthy(port):
        print(f"Shim is running on http://{DEFAULT_HOST}:{port} with pid {pid}.")
        return 0
    if _pid_running(pid):
        print(f"Shim process {pid} exists but health check failed.")
        return 1
    print("Shim is stopped.")
    return 1


def ensure_started(settings_path: Path, port: int) -> None:
    if not (_pid_running(_read_pid()) and _healthy(port)):
        code = start(settings_path, port)
        if code:
            raise SystemExit(code)


def exec_codex(settings_path: Path, port: int, codex_args: list[str]) -> None:
    overrides = _override_args(settings_path, port)
    codex_args = list(codex_args or [])
    if codex_args[:1] == ["--"]:
        codex_args = codex_args[1:]
    args = ["codex", *overrides, *codex_args]
    os.execvp("codex", args)


def exec_codex_app(settings_path: Path, port: int, path: str) -> None:
    _quit_codex_app()
    args = ["codex", "app", path]
    subprocess.Popen(args)
    _foreground_codex_app()


def _quit_codex_app() -> None:
    script = 'tell application "Codex" to if it is running then quit'
    try:
        subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
    except OSError:
        pass


CODEX_APP = Path("/Applications/Codex.app")
CODEX_APP_ASAR = CODEX_APP / "Contents" / "Resources" / "app.asar"
CODEX_APP_INFO_PLIST = CODEX_APP / "Contents" / "Info.plist"
ASAR_BACKUP = RUNTIME_DIR / "app.asar.before-codex-shim-model-picker-patch"
INFO_PLIST_BACKUP = RUNTIME_DIR / "Info.plist.before-codex-shim-model-picker-patch"
BUNDLE_BACKUP_LINK = RUNTIME_DIR / "Codex.app.bundle-backup"


def patch_codex_app() -> int:
    """Patch Codex Desktop's model-picker allowlist.

    macOS App Management (Ventura+) blocks any in-place modification of files
    inside notarized app bundles under /Applications, even with sudo, unless
    the calling terminal has been granted that TCC entitlement. To stay out of
    that swamp, we replace the *whole* bundle: copy Codex.app into the user's
    workdir, patch + repack + re-sign there, then atomically swap it into
    /Applications. App Management does not gate adding/removing entries in
    /Applications itself; only modifying an existing bundle in-place.
    """
    needle = "let u=c.useHiddenModels&&o!==`amazonBedrock`,d;"
    replacement = "let u=!1,d;"

    if not CODEX_APP_ASAR.exists():
        print(f"Codex app bundle not found at {CODEX_APP}.", file=sys.stderr)
        return 1
    if not _has_command("npx"):
        print("npx is required to patch the Electron asar bundle.", file=sys.stderr)
        return 1

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if not ASAR_BACKUP.exists():
        ASAR_BACKUP.write_bytes(CODEX_APP_ASAR.read_bytes())
        print(f"Backed up original app.asar to {ASAR_BACKUP}.")
    if not INFO_PLIST_BACKUP.exists():
        INFO_PLIST_BACKUP.write_bytes(CODEX_APP_INFO_PLIST.read_bytes())
        print(f"Backed up original Info.plist to {INFO_PLIST_BACKUP}.")

    _quit_codex_app()

    work_bundle = RUNTIME_DIR / "Codex.app.work"
    if work_bundle.exists():
        shutil.rmtree(work_bundle)
    print(f"Copying Codex.app to {work_bundle} (this takes a few seconds) …")
    subprocess.run(["/bin/cp", "-R", str(CODEX_APP), str(work_bundle)], check=True)

    work_asar = work_bundle / "Contents" / "Resources" / "app.asar"
    work_plist = work_bundle / "Contents" / "Info.plist"
    extract_dir = RUNTIME_DIR / "app-asar-work"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)

    subprocess.run(
        ["npx", "--yes", "@electron/asar", "extract", str(work_asar), str(extract_dir)],
        check=True,
    )
    bundle_file = _find_model_queries_bundle(extract_dir, needle, replacement)
    if bundle_file is None:
        print("Could not find the expected model picker filter in Codex Desktop.", file=sys.stderr)
        return 1
    text = bundle_file.read_text()
    if replacement in text and needle not in text:
        print("Codex Desktop model picker patch is already applied (in source bundle).")
    elif needle in text:
        bundle_file.write_text(text.replace(needle, replacement))
        print("Patched Codex Desktop model picker allowlist filter.")
    else:
        print("Could not find the expected model picker filter in Codex Desktop.", file=sys.stderr)
        return 1

    new_asar = RUNTIME_DIR / "app.asar.new"
    if new_asar.exists():
        new_asar.unlink()
    subprocess.run(
        ["npx", "--yes", "@electron/asar", "pack", str(extract_dir), str(new_asar)],
        check=True,
    )
    if not new_asar.exists() or new_asar.stat().st_size == 0:
        print(f"Repacked asar at {new_asar} is missing or empty.", file=sys.stderr)
        return 1
    if needle.encode() in new_asar.read_bytes():
        print("Repacked asar still contains the unpatched needle; aborting.", file=sys.stderr)
        return 1

    # Replace the asar inside the work bundle (user-owned, no TCC barrier).
    work_asar.write_bytes(new_asar.read_bytes())

    new_hash = _asar_header_sha256(work_asar)
    print(f"New asar header SHA-256: {new_hash}")
    subprocess.run(
        [
            "/usr/libexec/PlistBuddy",
            "-c",
            f"Set :ElectronAsarIntegrity:Resources/app.asar:hash {new_hash}",
            str(work_plist),
        ],
        check=True,
    )

    # Ad-hoc re-sign the work bundle. No sudo needed: we own this copy.
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(work_bundle)],
        check=True,
    )
    print("Re-signed work bundle.")

    # Now swap into /Applications. We need sudo for /Applications itself.
    if not _sudo_prime():
        print("sudo authentication is required to install the patched bundle into /Applications.", file=sys.stderr)
        return 1

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    bundle_backup = CODEX_APP.with_name(f"Codex.app.unpatched-{timestamp}")
    print(f"Moving original /Applications/Codex.app to {bundle_backup} …")
    subprocess.run(["sudo", "/bin/mv", str(CODEX_APP), str(bundle_backup)], check=True)
    try:
        subprocess.run(["sudo", "/bin/mv", str(work_bundle), str(CODEX_APP)], check=True)
    except subprocess.CalledProcessError:
        # Roll back so the user isn't left without Codex installed.
        subprocess.run(["sudo", "/bin/mv", str(bundle_backup), str(CODEX_APP)], check=False)
        raise

    # Drop a pointer to the latest backup so `restore-app` can find it.
    if BUNDLE_BACKUP_LINK.exists() or BUNDLE_BACKUP_LINK.is_symlink():
        BUNDLE_BACKUP_LINK.unlink()
    BUNDLE_BACKUP_LINK.symlink_to(bundle_backup)

    print(f"Installed patched Codex.app. Original bundle preserved at {bundle_backup}.")
    print("Launch with: codex-shim app .")
    return 0


def restore_codex_app_bundle() -> int:
    """Roll back to the original /Applications/Codex.app bundle."""
    if BUNDLE_BACKUP_LINK.exists() or BUNDLE_BACKUP_LINK.is_symlink():
        try:
            target = Path(os.readlink(BUNDLE_BACKUP_LINK))
        except OSError:
            target = BUNDLE_BACKUP_LINK
        if target.exists():
            if not _sudo_prime():
                print("sudo authentication is required to restore /Applications/Codex.app.", file=sys.stderr)
                return 1
            _quit_codex_app()
            subprocess.run(["sudo", "/bin/rm", "-rf", str(CODEX_APP)], check=True)
            subprocess.run(["sudo", "/bin/mv", str(target), str(CODEX_APP)], check=True)
            BUNDLE_BACKUP_LINK.unlink(missing_ok=True)
            print(f"Restored {CODEX_APP} from {target}.")
            return 0
        print(f"Backup symlink {BUNDLE_BACKUP_LINK} → {target} is dangling.", file=sys.stderr)

    # Fallback: search for the most recent Codex.app.unpatched-* sibling.
    candidates = sorted(CODEX_APP.parent.glob("Codex.app.unpatched-*"))
    if candidates:
        target = candidates[-1]
        if not _sudo_prime():
            print("sudo authentication is required to restore /Applications/Codex.app.", file=sys.stderr)
            return 1
        _quit_codex_app()
        subprocess.run(["sudo", "/bin/rm", "-rf", str(CODEX_APP)], check=True)
        subprocess.run(["sudo", "/bin/mv", str(target), str(CODEX_APP)], check=True)
        print(f"Restored {CODEX_APP} from {target}.")
        return 0

    print("No Codex.app.unpatched-* backup found in /Applications.", file=sys.stderr)
    return 1


def _has_command(command: str) -> bool:
    from shutil import which

    return which(command) is not None


def _find_model_queries_bundle(workdir: Path, needle: str, replacement: str) -> Path | None:
    assets_dir = workdir / "webview" / "assets"
    if not assets_dir.exists():
        return None
    candidates = sorted(assets_dir.glob("model-queries-*.js"))
    candidates.extend(p for p in sorted(assets_dir.glob("*.js")) if p not in candidates)
    for path in candidates:
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            text = path.read_text(errors="ignore")
        if needle in text or replacement in text:
            return path
    return None


def _sudo_prime() -> bool:
    """Trigger one sudo password prompt up front so later sudo calls run silently."""
    try:
        subprocess.run(["sudo", "-v"], check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def _asar_header_sha256(asar_path: Path) -> str:
    """SHA-256 of the asar JSON header (what ElectronAsarIntegrity checks)."""
    with asar_path.open("rb") as f:
        _, _, _, json_size = struct.unpack("<4I", f.read(16))
        header_json = f.read(json_size)
    return hashlib.sha256(header_json).hexdigest()


def _foreground_codex_app() -> None:
    script = '''
tell application "Codex" to activate
delay 0.5
tell application "System Events"
  if exists process "Codex" then
    tell process "Codex"
      set frontmost to true
      if (count of windows) is 0 then
        keystroke "n" using command down
        delay 0.3
      end if
      if (count of windows) > 0 then
        set position of window 1 to {80, 60}
        set size of window 1 to {1400, 980}
      end if
    end tell
  end if
end tell
'''
    try:
        subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _managed_config_blocks(default_slug: str, port: int) -> tuple[str, str]:
    top_block = f'''{MANAGED_BEGIN}
model = "{default_slug}"
model_provider = "factory_byok_shim"
model_catalog_json = "{CATALOG_PATH}"
{MANAGED_END}
'''

    provider_block = f'''{MANAGED_BEGIN}
[model_providers.factory_byok_shim]
name = "Factory BYOK Shim"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
experimental_bearer_token = "dummy"
request_max_retries = 3
stream_max_retries = 3
stream_idle_timeout_ms = 600000
{MANAGED_END}
'''
    return top_block, provider_block


def _remove_managed_config(text: str) -> str:
    while MANAGED_BEGIN in text:
        before, rest = text.split(MANAGED_BEGIN, 1)
        if MANAGED_END not in rest:
            return before
        _, after = rest.split(MANAGED_END, 1)
        text = before + after
    return text


def _remove_top_level_keys(text: str, keys: set[str]) -> str:
    lines = text.splitlines()
    output: list[str] = []
    in_top_level = True
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_top_level = False
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if in_top_level and key in keys:
            continue
        output.append(line)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def _remove_section(text: str, section: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    header = f"[{section}]"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped == header
            if skipping:
                continue
        if not skipping:
            output.append(line)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def _override_args(settings_path: Path, port: int) -> list[str]:
    models = FactorySettings(settings_path).load()
    default_slug = default_model_slug(models)
    pairs = codex_config_overrides(CATALOG_PATH, default_slug, port)
    args: list[str] = []
    for pair in pairs:
        args.extend(["-c", pair])
    return args


def _resolve_model_slug(models, requested: str | None) -> str:
    if requested is None:
        return _current_managed_model() or default_model_slug(models)
    by_slug = {model.slug: model.slug for model in models}
    by_model = {}
    for model in models:
        by_model.setdefault(model.model, []).append(model.slug)
    if requested in by_slug:
        return requested
    if requested in by_model and len(by_model[requested]) == 1:
        return by_model[requested][0]
    matches = [model.slug for model in models if requested.lower() in model.display_name.lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise SystemExit(f"Ambiguous model {requested!r}. Matches: {', '.join(matches)}")
    raise SystemExit(f"Unknown shim model {requested!r}. Run: codex-shim model list")


def _current_managed_model() -> str | None:
    if not CODEX_CONFIG_PATH.exists():
        return None
    for line in CODEX_CONFIG_PATH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("model = "):
            return stripped.split("=", 1)[1].strip().strip('"')
    return None


def _healthy(port: int) -> bool:
    try:
        with urlopen(f"http://{DEFAULT_HOST}:{port}/health", timeout=0.5) as response:
            return response.status == 200
    except Exception:
        return False


def _read_pid() -> int | None:
    try:
        return int(PID_PATH.read_text().strip())
    except Exception:
        return None


def _pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
