"""Bring a claimed Try export into this user's home, after a review.

Runs as the owner in a floating terminal, from the offer's notification. It
takes the export through the package's sudo handoff, asks for the transfer
passphrase, shows what the import brings and leaves, and imports after a
confirmation. The passphrase reaches `plan` and `apply` through a pipe.
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import contract
from .display import shown
from .categories import CATEGORIES
from .handoff import INCOMING
from .offer import FALLBACK_PATH, user_state

LIB = Path(__file__).resolve().parent.parent
BOOT = "import sys; sys.path.insert(0, sys.argv.pop(1)); from omarchy_migration.__main__ import main; sys.exit(main())"
HANDOFF = ("sudo", "-n", str(LIB / "handoff"))
SKEL = Path("/etc/skel")
ATTEMPTS = 5
SHOWN = 12
NAMES = {"files-and-projects": "Files and projects", "configuration": "Settings", "caches": "Caches"}


class ImportStopped(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Terminal:
    """Prompts through gum when it is installed, plain terminal prompts otherwise."""

    def __init__(self, gum=None):
        self.gum = gum

    def say(self, text=""):
        print(text, flush=True)

    def heading(self, text):
        if self.gum:
            subprocess.run([self.gum, "style", "--bold", "--margin", "1 0", text], check=False)
        else:
            print(f"\n{text}\n", flush=True)

    def secret(self, prompt):
        if self.gum:
            result = subprocess.run([self.gum, "input", "--password", "--placeholder", prompt],
                                    stdout=subprocess.PIPE, check=False)
            if result.returncode:
                return None
            return result.stdout.rstrip(b"\n")
        import getpass
        try:
            return getpass.getpass(f"{prompt}: ").encode()
        except (EOFError, KeyboardInterrupt):
            return None

    def choose(self, prompt, options):
        if self.gum:
            self.say(prompt)
            result = subprocess.run([self.gum, "choose", *options], stdout=subprocess.PIPE, text=True, check=False)
            choice = result.stdout.strip()
            return choice if choice in options else None
        self.say(prompt)
        for number, option in enumerate(options, 1):
            self.say(f"  {number}. {option}")
        try:
            answer = input("Choose a number: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return options[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(options) else None

    def confirm(self, prompt):
        """Yes unless the person says no or closes the prompt; Enter means yes."""
        if self.gum:
            return subprocess.run([self.gum, "confirm", "--affirmative", "Yes", "--negative", "No", prompt],
                                  check=False).returncode == 0
        try:
            answer = input(f"{prompt} [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("", "y", "yes")

    def pause(self):
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("Press Enter to close.")


def omarchy_command(name):
    """An Omarchy command on this session's PATH, or where Omarchy installs them."""
    search = [*os.environ.get("PATH", "").split(os.pathsep)]
    if os.environ.get("OMARCHY_PATH"):
        search.append(os.path.join(os.environ["OMARCHY_PATH"], "bin"))
    search.extend(FALLBACK_PATH)
    return shutil.which(name, path=os.pathsep.join(search))


def current_theme(home):
    with contextlib.suppress(OSError, UnicodeDecodeError):
        return Path(home, ".local/state/omarchy/current/theme.name").read_text().strip()
    return None


def apply_omarchy(ui, home, choices):
    """Applies the Try home's theme and background with Omarchy's own commands."""
    theme, background = choices.get("theme"), choices.get("background")
    if theme and theme != current_theme(home):
        command = omarchy_command("omarchy-theme-set")
        if command and subprocess.run([command, theme], stdout=subprocess.DEVNULL, check=False).returncode == 0:
            ui.say(f"Theme: {theme}.")
        else:
            ui.say(f"The theme {theme} couldn't be applied. Choose it from the Omarchy menu.")
    if background:
        path = Path(home, background)
        command = omarchy_command("omarchy-theme-bg-set")
        if command and path.is_file():
            subprocess.run([command, str(path)], stdout=subprocess.DEVNULL, check=False)


def _private(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def waiting_exports(home):
    """Exports in this home's incoming folder that this user hasn't imported or dismissed."""
    folder = Path(home, *INCOMING)
    records = user_state(home)
    found = []
    with contextlib.suppress(OSError):
        for name in sorted(os.listdir(folder)):
            with contextlib.suppress(contract.ContractError):
                contract._uuid(name, "$")
                done = (records / "imported" / f"{name}.json").exists() or (records / "dismissed" / name).exists()
                if not done and (folder / name / "bundle.age").is_file() and (folder / name / "receipt.json").is_file():
                    found.append(name)
    return found


def run_review(operation, secret, arguments, migration):
    """Run plan or apply with the passphrase on a pipe; returns (document, error code)."""
    read, write = os.pipe()
    try:
        os.write(write, secret + b"\n")
        os.close(write)
        write = None
        result = subprocess.run([*migration, operation, *arguments, "--passphrase-fd", str(read)],
                                pass_fds=(read,), capture_output=True, text=True, check=False)
    finally:
        os.close(read)
        if write is not None:
            os.close(write)
    if result.returncode == 0:
        return json.loads(result.stdout), None
    try:
        return None, json.loads(result.stderr.strip().splitlines()[-1])["error"]
    except (ValueError, IndexError, KeyError, TypeError):
        return None, "review_operation_failed"


def plan_totals(summary):
    totals = dict.fromkeys(("create", "replace", "present", "conflict", "inert"), 0)
    for group in summary["categories"]:
        for key in totals:
            totals[key] += group[key]
    return totals


def summary_lines(summary):
    """The plan's summary for a person: what comes, what replaces a default, what stays, what's left out."""
    totals = plan_totals(summary)
    created = [f"{NAMES.get(group['id'], group['id'])} {group['create']}"
               for group in summary["categories"] if group["create"]]
    lines = [f"Comes across: {totals['create']} files" + (f" ({', '.join(created)})" if created else "")]
    replaced = summary["replaces_default"]
    lines.append(f"Replaces untouched Omarchy defaults: {len(replaced)} files, each with a backup")
    lines.extend(f"    {shown(path)}" for path in replaced[:SHOWN])
    if len(replaced) > SHOWN:
        lines.append(f"    and {len(replaced) - SHOWN} more")
    kept = summary["kept"]
    lines.append(f"Stays as it is here, because this Mac's file differs: {len(kept)}")
    lines.extend(f"    {shown(item['path'])}" for item in kept[:SHOWN])
    if len(kept) > SHOWN:
        lines.append(f"    and {len(kept) - SHOWN} more")
    if totals["present"]:
        lines.append(f"Already here: {totals['present']} files")
    left_out = sum(summary["left_out"].values()) + totals["inert"]
    lines.append(f"Left out by Try Omarchy's export policy: {left_out}"
                 " (credentials, Mac-only settings and links into the Mac's shared folder)")
    return lines


def _remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)


def _record(path, value):
    _private(path.parent)
    temporary = path.with_name(path.name + ".partial")
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(json.dumps(value, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def import_export(ui, home, work, export_id, *, migration, defaults=SKEL):
    """Review and import one export. Returns "imported", "later" or "dismissed"."""
    folder = Path(home, *INCOMING, export_id)
    job = _private(Path(work) / "jobs" / export_id)
    scratch = _private(Path(work) / "scratch")
    summary_path = Path(work) / f"summary-{export_id}.json"
    common = ["--bundle", str(folder / "bundle.age"), "--receipt", str(folder / "receipt.json"),
              "--target", str(home), "--job", str(job), "--scratch", str(scratch), "--defaults", str(defaults)]
    plan = None
    for attempt in range(ATTEMPTS):
        secret = ui.secret("Transfer passphrase")
        if not secret:
            return "later"
        with contextlib.suppress(FileNotFoundError):
            summary_path.unlink()
        plan, error = run_review("plan", secret, [*common, "--summary", str(summary_path)], migration)
        if error == "passphrase_incorrect":
            ui.say("That passphrase doesn't open this export. Try again.")
            continue
        if error:
            raise ImportStopped(error)
        break
    if plan is None:
        return "later"
    summary = json.loads(summary_path.read_text())
    summary_path.unlink()
    ui.heading("What the import does")
    for line in summary_lines(summary):
        ui.say(line)
    theme = (summary.get("omarchy") or {}).get("theme")
    if theme and theme != current_theme(home):
        ui.say(f"Theme: {theme}, applied after the import")
    ui.say()
    choice = ui.choose("Import these files now?", ["Import", "Not now", "Don't offer again"])
    if choice == "Don't offer again":
        _record(user_state(home) / "dismissed" / export_id, {"export_id": export_id})
        _remove_tree(folder)
        _remove_tree(scratch)
        return "dismissed"
    if choice != "Import":
        return "later"
    report, error = run_review("apply", secret, [*common, "--plan-id", plan["plan_id"]], migration)
    if error:
        raise ImportStopped(error)
    _record(user_state(home) / "imported" / f"{export_id}.json",
            {"export_id": export_id, "plan_id": plan["plan_id"], "job_id": report["job_id"],
             "categories": report["categories"]})
    _remove_tree(folder)
    _remove_tree(scratch)
    replaced = len(summary["replaces_default"])
    conflicts = sum(item["conflicts"] for item in report["categories"])
    ui.say(f"Imported {plan_totals(summary)['create']} files"
           + (f" and replaced {replaced} untouched defaults" if replaced else "") + "."
           + (f" {conflicts} stayed as they were here." if conflicts else ""))
    if summary["replaces_default"]:
        ui.say(f"Backups of the replaced defaults are in {job}.")
    apply_omarchy(ui, home, summary.get("omarchy") or {})
    return "imported"


MESSAGES = {
    "not_run_by_sudo": "The handoff needs sudo.",
    "user_refused": "This account can't take the export.",
    "unsafe_home": "Your home folder has a link or a folder that isn't yours where the export goes.",
    "insufficient_space": "There isn't enough free space for the import.",
    "plan_changed": "Something in your home changed since the review. Run the import again.",
    "receipt_mismatch": "The export doesn't match its receipt.",
}


def _handoff(command):
    result = subprocess.run(list(command), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    if result.returncode:
        try:
            raise ImportStopped(json.loads(result.stderr.strip().splitlines()[-1])["error"])
        except (ValueError, IndexError, KeyError, TypeError):
            raise ImportStopped("handoff_failed") from None
    return json.loads(result.stdout)


def reboot_system():
    """Reboots the way Omarchy's menu does, closing apps first; False if it didn't start."""
    command = omarchy_command("omarchy-system")
    return subprocess.run([command, "reboot"] if command else ["systemctl", "reboot"], check=False).returncode == 0


def run(ui, home, *, handoff=HANDOFF, migration=None, defaults=SKEL, reboot=reboot_system):
    migration = migration or [sys.executable, "-I", "-c", BOOT, str(LIB)]
    ui.heading("Bring your files from Try Omarchy")
    try:
        work = Path(_handoff(handoff)["work"])
        exports = waiting_exports(home)
        if not exports:
            ui.say("No export from Try Omarchy is waiting.")
            return 0
        outcomes = [import_export(ui, home, work, export_id, migration=migration, defaults=defaults)
                    for export_id in exports]
    except ImportStopped as error:
        ui.say(f"The import stopped: {MESSAGES.get(error.code, error.code)}")
        ui.say("Your export is still here; the offer comes back at your next login.")
        return 1
    if "imported" in outcomes:
        if ui.confirm("Reboot now so every app picks up your settings?"):
            if reboot():
                ui.rebooting = True
                return 0
            ui.say("The reboot didn't start. Reboot from the Omarchy menu.")
        else:
            ui.say("Reboot when you're ready, so every app picks up your settings.")
    elif "later" in outcomes:
        ui.say("Nothing was imported. The offer comes back at your next login.")
    elif "dismissed" in outcomes:
        ui.say("Nothing was imported. The export is still in Try Omarchy's Exports on the Mac.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--defaults", type=Path, default=SKEL, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    ui = Terminal(shutil.which("gum"))
    status = run(ui, Path.home(), defaults=arguments.defaults)
    if not getattr(ui, "rebooting", False):
        ui.pause()
    return status


if __name__ == "__main__":
    sys.exit(main())
