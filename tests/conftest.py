"""Shared fixtures: the two workflow-language checkers used as output oracles.

A generated wrapper is only meaningful if the target language accepts it, so the
tests run the real checkers rather than comparing against golden strings.  Neither
checker is a Python dependency of this package; when one is missing its tests skip
rather than fail, so the suite stays runnable on a bare checkout.
"""
import shutil
import subprocess

import pytest


@pytest.fixture(scope="session")
def example_dir(pytestconfig):
    """Directory holding the captured `--help` output of real tools."""
    return pytestconfig.rootpath / "tests" / "example"


@pytest.fixture(scope="session")
def help_text(example_dir):
    """Read one of the bundled help-text captures by short name.

    The naming convention is the registry; `tests/example/Makefile` depends on it
    too, so a second table mapping name to filename would be a third thing to keep
    in step.
    """

    def read(name):
        return (example_dir / f"help-{name}.txt").read_text()

    return read


def _run_checker(executable, args, source, tmp_path, suffix):
    if shutil.which(executable) is None:
        pytest.skip(f"{executable} is not installed")
    path = tmp_path / f"generated{suffix}"
    path.write_text(source)
    done = subprocess.run(
        [executable, *args, str(path)], capture_output=True, text=True, check=False
    )
    return done, path


@pytest.fixture
def check_wdl(tmp_path):
    """Assert that a WDL document is accepted by `miniwdl check`."""

    def check(source):
        done, path = _run_checker("miniwdl", ["check"], source, tmp_path, ".wdl")
        assert done.returncode == 0, (
            f"miniwdl check rejected the generated WDL:\n{done.stdout}{done.stderr}\n"
            f"--- generated ---\n{source}"
        )
        return path

    return check


@pytest.fixture
def check_nextflow(tmp_path):
    """Assert that a Nextflow script is accepted by `nextflow lint`."""

    def check(source):
        done, path = _run_checker("nextflow", ["lint"], source, tmp_path, ".nf")
        assert done.returncode == 0, (
            f"nextflow lint rejected the generated script:\n"
            f"{done.stdout}{done.stderr}\n--- generated ---\n{source}"
        )
        return path

    return check
