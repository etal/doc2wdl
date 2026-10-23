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


@pytest.fixture
def wdl_task():
    """Parse and type check a one-task WDL document with miniwdl's Python API.

    A checker can say only that a task is valid.  What a task does -- the values
    its declarations hold, the command line it runs -- needs the evaluator.
    """
    WDL = pytest.importorskip("WDL")

    def parse(source):
        document = WDL.parse_document(source)
        document.typecheck()
        (task,) = document.tasks
        return task

    return parse


@pytest.fixture
def wdl_command(wdl_task):
    """Evaluate a one-task WDL document's command line for the given input values.

    Values are given as JSON-compatible Python, as in a WDL inputs file.  An input
    left out takes its declared default, or stays unset.  Line continuations are
    joined and runs of whitespace collapsed, so the result reads as the shell would
    split it.
    """
    import WDL

    def evaluate(source, **inputs):
        task = wdl_task(source)
        unknown = set(inputs) - {decl.name for decl in task.inputs}
        assert not unknown, f"no such inputs: {unknown}"
        stdlib = WDL.StdLib.Base("1.1")
        env = WDL.Env.Bindings()
        for decl in task.inputs:
            if decl.name in inputs:
                value = WDL.Value.from_json(decl.type, inputs[decl.name])
            elif decl.expr is not None:
                value = decl.expr.eval(env, stdlib)
            else:
                value = WDL.Value.Null()
            env = env.bind(decl.name, value)
        command = task.command.eval(env, stdlib).value
        return " ".join(command.replace("\\\n", " ").split())

    return evaluate
