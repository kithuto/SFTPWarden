"""SSH project ownership must span several commands and survive uncertain replies."""

import pytest

from sftpwarden.contexts import RemoteEndpoint
from sftpwarden.remote.locking import operation_lock
from sftpwarden.system.commands import CommandResult
from sftpwarden.utils.errors import RuntimeError


@pytest.mark.parametrize("failure", ["acquire", "work", "cleanup", None])
def test_remote_operation_releases_only_its_own_receipt(failure):
    remote = RemoteEndpoint(
        host="example.invalid",
        user="operator",
        remote_root="~/project 'quoted",
        remote_config="~/project 'quoted/sftpwarden.yaml",
    )
    scripts = []
    entered = []

    def runner(command):
        scripts.append(command[-1])
        fail = len(scripts) == (1 if failure == "acquire" else 2) and failure in {
            "acquire",
            "cleanup",
        }
        return CommandResult(command, int(fail), "", "private diagnostics")

    def work():
        with operation_lock(remote, create_root=True, runner=runner):
            entered.append(True)
            if failure == "work":
                raise ValueError("work failed")

    if failure is None:
        work()
    else:
        with pytest.raises(ValueError if failure == "work" else RuntimeError):
            work()
    assert entered == ([] if failure == "acquire" else [True])
    assert len(scripts) == 2
    nonce = scripts[0].split("printf '%s' ", 1)[1].split()[0]
    assert nonce in scripts[1]
    assert "test ! -L .sftpwarden-operation.lock/owner" in scripts[1]
    assert "rmdir .sftpwarden-operation.lock" in scripts[1]
    assert 'root="$HOME"/' in scripts[0]
