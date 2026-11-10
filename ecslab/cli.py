"""ecslab: preflight, start, run <scenario>, check, hint, reset."""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import click

from . import config, emulator, progress, replay, runner


def _sh(cmd, check=True):
    p = subprocess.run(cmd, cwd=config.ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and p.returncode != 0:
        raise click.ClickException(f"{' '.join(cmd)} failed:\n{p.stdout[-1500:]}")
    return p.stdout.strip()


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _proxy_pid():
    try:
        pid = int(config.PROXY_PID.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _stop_proxy():
    pid = _proxy_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
    config.PROXY_PID.unlink(missing_ok=True)


def _start_proxy():
    if _proxy_pid():
        return
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    if _port_open(config.PROXY_PORT):
        raise click.ClickException(f"port {config.PROXY_PORT} is already in use by something else")
    log = config.PROXY_LOG.open("a")
    p = subprocess.Popen([sys.executable, "-m", "ecslab.proxy"], cwd=config.ROOT, stdout=log, stderr=log,
                         start_new_session=True)
    config.PROXY_PID.write_text(str(p.pid))
    for _ in range(30):
        if _port_open(config.PROXY_PORT):
            return
        time.sleep(0.2)
    raise click.ClickException(f"the logging proxy did not start; see {config.PROXY_LOG}")


def _wait_for_floci(aws=None, timeout=120):
    try:
        emulator.wait(timeout)
    except emulator.EmulatorError as e:
        raise click.ClickException(str(e))


_remove_task_containers = emulator.remove_task_containers


def _all_images():
    images = {}
    for sid in runner.available():
        for img in runner.load(sid)["seed_spec"].get("images", []):
            images[img["tag"]] = img
    return images.values()


def _require_active():
    state = runner.load_state()
    if not state:
        raise click.ClickException("no scenario is running. Start one with: ecslab run <scenario>")
    return state, runner.load(state["scenario"])


@click.group()
@click.version_option()
def main():
    """ECS Lab: break-and-fix ECS scenarios on your own machine."""


@main.command()
@click.pass_context
def doctor(ctx):
    """Check this machine can run the lab (same as preflight)."""
    ctx.invoke(preflight, full=False)


@main.command()
@click.option("--full", is_flag=True, help="Run the full Floci capability probe (about six minutes).")
def preflight(full):
    """Check this machine can run the lab."""
    if full:
        sys.exit(subprocess.call([sys.executable, str(config.ROOT / "preflight" / "preflight.py")]))
    ok = True

    def row(good, label, fix=""):
        nonlocal ok
        ok = ok and good
        click.echo(f"  [{'ok' if good else 'FAIL'}] {label}" + (f"\n         {fix}" if not good and fix else ""))

    row(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "Python 3.10 or newer is required")
    docker = shutil.which("docker") is not None
    row(docker, "docker is installed", "Install Docker Desktop")
    if docker:
        row(subprocess.run(["docker", "info"], capture_output=True).returncode == 0,
            "Docker daemon is running", "Start Docker Desktop")
        row(subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0,
            "docker compose is available")
    if docker and not config.mock_enabled():
        have = subprocess.run(["docker", "images", "-q", "floci/floci"], capture_output=True, text=True).stdout.strip()
        row(bool(have), "Floci image is downloaded", "It is pulled the first time you run: ecslab start (needs the internet once)")
        free = shutil.disk_usage(config.ROOT).free / 1e9
        row(free > 3, f"{free:.0f} GB of free disk space", "Docker images and containers need a few GB free")
    row(not _port_open(4566) or emulator.current_env() is not None, "port 4566 is free for the emulator",
        "Something other than the lab's Floci is listening on 4566")
    row(shutil.which("aws") is not None, "AWS CLI is installed",
        "Install it: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html")
    ours = _proxy_pid() is not None
    row(ours or not _port_open(config.PROXY_PORT), f"port {config.PROXY_PORT} is free for the lab proxy",
        "Something else is listening there")
    click.echo("\nReady. Next: ecslab start" if ok else "\nFix the items marked FAIL, then run this again.")
    sys.exit(0 if ok else 1)


@main.command()
def start():
    """Build the task images, start Floci and the logging proxy."""
    if config.mock_enabled():
        click.echo("Mock mode: no Docker, no proxy.")
        return
    click.echo("Building task images ...")
    for img in _all_images():
        args = [a for k, v in (img.get("build_args") or {}).items() for a in ("--build-arg", f"{k}={v}")]
        _sh(["docker", "build", "-q", "-t", img["tag"], *args, str(config.ROOT / img["context"])])
    click.echo("Starting Floci ...")
    _sh(["docker", "compose", "up", "-d"])
    _wait_for_floci(config.clients())
    _start_proxy()
    click.echo(f"Ready. Emulator on {config.FLOCI_URL}, lab proxy on {config.PROXY_URL}.")
    click.echo("Next:  ecslab ui      (web interface)")
    click.echo("  or:  " + "\n       ".join(f"ecslab run {sid}" for sid in runner.available()) + "      (terminal only)")


@main.command()
@click.argument("scenario_id", metavar="SCENARIO")
def run(scenario_id):
    """Set up SCENARIO and hand you the task."""
    try:
        scenario = runner.load(scenario_id)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    aws = config.clients()
    if not config.mock_enabled():
        _wait_for_floci(aws, timeout=10)
        _start_proxy()
    click.echo("Setting up the environment (about 30 seconds) ...")
    try:
        if emulator.ensure(scenario["seed_spec"].get("emulator")):   # this scenario needs other Floci settings
            aws = config.clients()
        runner.start(aws, scenario)
    except Exception as e:
        raise click.ClickException(f"could not set up the scenario: {e}")
    click.echo(f"\n{scenario['title']}")
    click.echo(f"Difficulty {scenario['difficulty']}/5, about {scenario['estimated_minutes']} minutes\n")
    click.echo("".join(f"  {l}\n" for l in scenario["brief"].rstrip().splitlines()))
    click.echo("Two ways to work:\n")
    click.echo("  ecslab ui                  web interface with a built-in terminal (nothing to set up)")
    click.echo('  eval "$(ecslab env)"       or point the AWS CLI in this terminal at the lab\n')
    click.echo("When you think it is fixed: ecslab check     Stuck: ecslab hint")


@main.command()
@click.option("--wait", default=45, show_default=True, help="Seconds to keep polling for a fix to settle.")
def check(wait):
    """Verify whether you actually fixed it."""
    state, scenario = _require_active()
    aws = config.clients()
    settle = 0 if config.mock_enabled() else 8
    deadline = time.time() + wait
    while True:
        outcome = runner.check(aws, scenario, settle_seconds=settle)
        if outcome.status != runner.FAILING or time.time() >= deadline or config.mock_enabled():
            break
        time.sleep(4)

    if outcome.status == runner.PASSED:
        first = not state.get("solved_at")
        if first:
            state["solved_at"] = time.time()
            runner.save_state(state)
        calls = runner.read_calls()
        text = replay.build(calls, scenario.get("replay"), state["started_at"], state["solved_at"])
        if first:
            progress.record_pass(scenario["id"], state["solved_at"] - state["started_at"], state.get("hints_shown", 0),
                                 len(calls), text)
        click.echo(text)
        click.echo("\nWhat this lab teaches:")
        for t in scenario.get("teaches", []):
            click.echo(f"  - {t}")
        if scenario.get("on_aws"):
            click.echo("\nOn real AWS:")
            for t in scenario["on_aws"]:
                click.echo(f"  - {t}")
        return
    for m in outcome.messages:
        click.echo(m)
    sys.exit(2)


@main.command()
def hint():
    """Show the next hint, if enough time has passed."""
    state, scenario = _require_active()
    click.echo(runner.next_hint(scenario, state))


@main.command()
@click.option("--port", default=None, type=int, help="Port for the web interface (default 4568).")
@click.option("--no-browser", is_flag=True, help="Print the link instead of opening it.")
def ui(port, no_browser):
    """Open the web interface: console pages plus a built-in terminal."""
    from .ui import server as ui_server
    if not config.mock_enabled():
        try:
            _wait_for_floci(config.clients(), timeout=5)
        except click.ClickException:
            raise click.ClickException("The emulator is not running. Run `ecslab start` first.")
        _start_proxy()
    try:
        httpd, _, url = ui_server.create(port)
    except OSError as e:
        raise click.ClickException(f"could not start the web interface: {e}")
    click.echo(f"ECS Lab is open at:\n\n  {url}\n")
    click.echo("The link is private to this run. Leave this running; press Ctrl-C to stop.")
    if not no_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@main.command()
@click.argument("scenarios", nargs=-1)
@click.option("--from", "start_at", default=None, help="Test this scenario and every one after it, e.g. --from ecs-015.")
def selftest(scenarios, start_at):
    """Prove scenarios work end to end on the emulator (all of them by default).

    For each one: seed it, spring the trap, confirm the fault appears as
    described, confirm every shortcut is rejected, apply the reference solution
    and confirm it passes. Takes a few minutes per scenario.
    """
    from . import selftest as st
    ids = list(scenarios) or runner.available()
    if start_at:
        ids = [i for i in runner.available() if i >= start_at]
        if not ids:
            raise click.ClickException(f"no scenario at or after '{start_at}'")
    aws = config.clients()
    if not config.mock_enabled():
        try:
            _wait_for_floci(aws, timeout=5)
        except click.ClickException:
            raise click.ClickException("The emulator is not running. Run `ecslab start` first.")

    def reset(scenario=None):
        for f in (config.STATE_FILE, config.CALL_LOG, config.MOCK_STATE):
            f.unlink(missing_ok=True)
        emulator.fresh((scenario or {}).get("seed_spec", {}).get("emulator"))

    failed = []
    for sid in ids:
        click.echo(f"Testing {sid} ...")
        try:
            rep = st.selftest(config.clients(), sid, reset=reset)
        except FileNotFoundError as e:
            raise click.ClickException(str(e))
        click.echo(st.format_report(rep) + "\n")
        if not rep.ok:
            failed.append(sid)
    reset()
    click.echo(f"{len(ids) - len(failed)} of {len(ids)} scenarios verified." + (f" Failed: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


@main.command()
def env():
    """Print the settings that point the AWS CLI at the lab.

    Use it as:  eval "$(ecslab env)"
    """
    for k, v in config.learner_env().items():
        click.echo(f"export {k}={v}")


@main.command()
@click.argument("scenario_id", metavar="SCENARIO")
def solution(scenario_id):
    """Print SCENARIO's reference fix. Spoilers."""
    from . import solution as sol
    try:
        click.echo(sol.describe(scenario_id))
    except FileNotFoundError as e:
        raise click.ClickException(str(e))


@main.command()
@click.option("--stop", is_flag=True, help="Also shut Floci and the proxy down.")
@click.option("--forget", is_flag=True, help="Also erase your progress: which scenarios you solved and your past replays.")
def reset(stop, forget):
    """Throw the environment away. Run a scenario again to start over."""
    if forget:
        progress.forget()
    for f in (config.STATE_FILE, config.CALL_LOG, config.MOCK_STATE):
        f.unlink(missing_ok=True)
    if config.mock_enabled():
        click.echo("Reset.")
        return
    _remove_task_containers()
    if stop:
        _stop_proxy()
        _sh(["docker", "compose", "down", "-v", "--remove-orphans"], check=False)
        click.echo("Stopped. Start again with: ecslab start")
        return
    emulator.fresh()     # Floci keeps state in memory, so a restart is a clean slate
    click.echo("Reset. Start a scenario with: ecslab run <scenario>")


if __name__ == "__main__":
    main()
