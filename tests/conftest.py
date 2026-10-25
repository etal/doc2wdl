"""Shared fixtures: the two workflow-language checkers used as output oracles.

A generated wrapper is only meaningful if the target language accepts it, so the
tests run the real checkers rather than comparing against golden strings.  Neither
checker is a Python dependency of this package; when one is missing its tests skip
rather than fail, so the suite stays runnable on a bare checkout.
"""

import os
import re
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
        return (example_dir / f"help-{name}.txt").read_text(encoding="utf-8")

    return read


@pytest.fixture(scope="session")
def corpus_dir(pytestconfig):
    """Directory holding the conformance corpus and its manifest."""
    return pytestconfig.rootpath / "tests" / "corpus"


@pytest.fixture(scope="session")
def corpus_text(corpus_dir):
    """Read one of the conformance corpus's help texts by the name its manifest uses."""

    def read(name):
        return (corpus_dir / f"help-{name}.txt").read_text(encoding="utf-8")

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


@pytest.fixture(scope="session")
def nextflow_launches():
    """Skip unless the `nextflow` launcher itself starts.

    The launcher refuses an unsupported Java -- 25.10 accepts 17 to 25 -- with exit
    status 1, which is also the status of a lint rejection, so without this probe a
    test expecting a rejection would pass for the wrong reason.  `-version` lints
    nothing, so its failure can only be the launcher's.
    """
    if shutil.which("nextflow") is None:
        pytest.skip("nextflow is not installed")
    done = subprocess.run(
        ["nextflow", "-version"], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        pytest.skip(f"nextflow cannot start: {done.stderr.strip()}")


@pytest.fixture
def check_nextflow(tmp_path, nextflow_launches):
    """Assert that `nextflow lint` accepts a Nextflow script without warnings.

    A warning still exits 0, and lint warns about syntax slated for removal, such as
    the implicit closure parameter `it`.
    """

    def check(source):
        done, path = _run_checker("nextflow", ["lint"], source, tmp_path, ".nf")
        warned = any(line.startswith("Warn ") for line in done.stdout.splitlines())
        assert done.returncode == 0 and not warned, (
            f"nextflow lint rejected or warned about the generated script:\n"
            f"{done.stdout}{done.stderr}\n--- generated ---\n{source}"
        )
        return path

    return check


def _groovy(value):
    """Spell a JSON-compatible Python value, other than None, as a Groovy literal.

    The caller spells an unset input `''` or `[]`, so None has no use here.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return "[" + ", ".join(_groovy(item) for item in value) + "]"


@pytest.fixture
def nextflow_commands(tmp_path, nextflow_launches):
    """Run a one-process Nextflow script and collect the command lines it renders.

    Each call is a sequence of input values, one per declared input in order, as
    JSON-compatible Python.  The process's command is prefixed with `echo`, so its
    standard output is the command line it would have run, with runs of whitespace
    collapsed.  Tasks finish in no fixed order, so the result is a set.  This takes
    a JVM start and several seconds, which is why the lint checker is the default
    oracle and this one is reserved for what only execution shows.
    """

    def run(source, *calls):
        (title,) = re.findall(r"^process (\w+) \{$", source, re.MULTILINE)
        echoed = source.replace(
            'script:\n    """\n    ', 'script:\n    """\n    echo ', 1
        )
        assert echoed != source, "no script block to prefix"
        channels = ", ".join(
            "channel.of(" + ", ".join(_groovy(value) for value in column) + ")"
            for column in zip(*calls, strict=True)
        )
        path = tmp_path / "run.nf"
        path.write_text(
            f"{echoed}\n\nworkflow {{\n"
            f"    {title}({channels}).stdout.view {{ it.trim() }}\n}}\n"
        )
        done = subprocess.run(
            ["nextflow", "-q", "run", str(path), "-work-dir", str(tmp_path / "work")],
            capture_output=True,
            text=True,
            check=False,
            cwd=tmp_path,
            env={**os.environ, "NXF_OFFLINE": "true"},
        )
        assert done.returncode == 0, f"nextflow run failed:\n{done.stdout}{done.stderr}"
        return {" ".join(line.split()) for line in done.stdout.splitlines()}

    return run


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
