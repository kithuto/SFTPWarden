"""Identity preflight, credential boundaries and activation failure contracts."""

from __future__ import annotations

import base64
import json
import signal
import subprocess
import time
from types import SimpleNamespace

import pytest

from sftpwarden.config import KubernetesMode, ProviderType, default_project_config
from sftpwarden.services import kubernetes_identity as identity
from sftpwarden.utils.errors import RuntimeError


def test_idle_helper_retains_its_deadline_and_handles_termination(monkeypatch):
    handlers = {}
    sleeps = []
    monkeypatch.setattr(
        signal, "signal", lambda number, handler: handlers.setdefault(number, handler)
    )
    monkeypatch.setattr(time, "sleep", sleeps.append)
    exec(identity.idle_helper_command()[2], {})  # noqa: S102
    assert sleeps == [600]
    with pytest.raises(SystemExit) as stopped:
        handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert stopped.value.code == 0


def _workload():
    return {
        "metadata": {"uid": "workload-uid", "resourceVersion": "7"},
        "spec": {
            "replicas": 1,
            "updateStrategy": {"type": "OnDelete"},
            "template": {
                "spec": {
                    "volumes": [{"name": "host-keys", "emptyDir": {}}],
                    "containers": [
                        {
                            "name": "sftpwarden",
                            "volumeMounts": [{"name": "host-keys", "mountPath": "/keys"}],
                        }
                    ],
                }
            },
        },
    }


def _pod(uid="old"):
    return {
        "metadata": {"name": "test-0", "uid": uid, "ownerReferences": [{"uid": "workload-uid"}]},
        "spec": {"nodeName": "node"},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def test_legacy_identity_is_seeded_before_receipt(monkeypatch):
    config = default_project_config("test")
    events = []
    resources = {"statefulset": _workload(), "pod": _pod()}
    monkeypatch.setattr(identity, "get_resource", lambda _cfg, kind, name: resources.get(kind))
    monkeypatch.setattr(
        identity, "_capture", lambda *_args: ({"private": "opaque"}, {"ed25519": "SHA256:public"})
    )
    monkeypatch.setattr(identity, "_seed_volume", lambda *_args: events.append("seed-verified"))
    monkeypatch.setattr(identity, "write_resource", lambda _cfg, doc: events.append(doc))

    identity.prepare_identity(config)

    assert events[0] == "seed-verified"
    assert events[1]["data"]["workloadUID"] == "workload-uid"
    assert "opaque" not in json.dumps(events[1])


@pytest.mark.parametrize("conflict", [False, True])
def test_legacy_activation_strategy_is_prepared_after_identity_before_receipt(
    monkeypatch, conflict
):
    config = default_project_config("test")
    workload = _workload()
    workload["spec"]["updateStrategy"] = {
        "type": "RollingUpdate",
        "rollingUpdate": {"partition": 0},
    }
    resources = {"statefulset": workload, "pod": _pod()}
    events = []
    monkeypatch.setattr(identity, "get_resource", lambda _cfg, kind, name: resources.get(kind))
    monkeypatch.setattr(identity, "_capture", lambda *_: ({}, {}))
    monkeypatch.setattr(identity, "_seed_volume", lambda *_: events.append("seed"))
    monkeypatch.setattr(identity, "write_resource", lambda *_: events.append("receipt"))

    def request(_config, arguments):
        assert arguments[:4] == ["patch", "statefulset", "test", "--type=json"]
        patch = json.loads(arguments[-1])
        assert patch[:2] == [
            {"op": "test", "path": "/metadata/uid", "value": "workload-uid"},
            {"op": "test", "path": "/metadata/resourceVersion", "value": "7"},
        ]
        assert patch[2] == {
            "op": "add",
            "path": "/spec/updateStrategy",
            "value": {"type": "OnDelete"},
        }
        events.append("strategy")
        if conflict:
            raise RuntimeError("concurrent workload change")
        return b""

    monkeypatch.setattr(identity, "request", request)
    if conflict:
        with pytest.raises(RuntimeError, match="concurrent"):
            identity.prepare_identity(config)
        assert events == ["seed", "strategy"]
    else:
        identity.prepare_identity(config)
        assert events == ["seed", "strategy", "receipt"]


def test_failed_seed_never_records_success(monkeypatch):
    config = default_project_config("test")
    resources = {"statefulset": _workload(), "pod": _pod()}
    monkeypatch.setattr(identity, "get_resource", lambda _cfg, kind, name: resources.get(kind))
    monkeypatch.setattr(identity, "_capture", lambda *_args: ({}, {}))

    def fail(*args):
        raise RuntimeError("unavailable volume")

    monkeypatch.setattr(identity, "_seed_volume", fail)
    monkeypatch.setattr(
        identity,
        "write_resource",
        lambda *_args: pytest.fail("receipt published after failed seed"),
    )
    with pytest.raises(RuntimeError, match="unavailable volume"):
        identity.prepare_identity(config)


def test_missing_old_pod_does_not_generate_another_identity(monkeypatch):
    config = default_project_config("test")
    monkeypatch.setattr(
        identity,
        "get_resource",
        lambda _cfg, kind, name: _workload() if kind == "statefulset" else None,
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        identity.prepare_identity(config)


def test_external_secret_is_checked_but_never_written(monkeypatch):
    config = default_project_config("test")
    config.kubernetes.host_keys_secret = "operator-keys"
    secret = {"metadata": {"resourceVersion": "1"}, "data": {"key": "opaque"}}
    resources = {"statefulset": _workload(), "pod": _pod(), "secret": secret}
    monkeypatch.setattr(identity, "get_resource", lambda _cfg, kind, name: resources.get(kind))
    fingerprints = {"ed25519": "SHA256:public"}
    monkeypatch.setattr(identity, "decode_keys", lambda _data: ({}, fingerprints))
    monkeypatch.setattr(identity, "_capture", lambda *_args: ({}, fingerprints))
    written = []
    monkeypatch.setattr(identity, "write_resource", lambda _cfg, doc: written.append(doc))
    identity.prepare_identity(config)
    assert [doc["kind"] for doc in written] == ["ConfigMap"]
    monkeypatch.setattr(identity, "_capture", lambda *_args: ({}, {"ed25519": "different"}))
    with pytest.raises(RuntimeError, match="differs"):
        identity.prepare_identity(config)
    assert len(written) == 1


def test_provider_secret_delivery_resolves_once_and_keeps_value_off_arguments(monkeypatch):
    config = default_project_config("test", ProviderType.POSTGRESQL, dsn="${TEST_PROVIDER_DSN}")
    secret = "postgresql://synthetic:p$ass;'${LITERAL}'@db/test"
    monkeypatch.setenv("TEST_PROVIDER_DSN", secret)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(identity.subprocess, "run", run)
    identity.prepare_provider_secret(config)
    assert secret not in json.dumps([item[0] for item in calls])
    payload = json.loads(calls[-1][1]["input"])
    assert base64.b64decode(payload["data"][identity.PROVIDER_DSN_ENV]).decode() == secret


def test_private_command_error_does_not_disclose_output(monkeypatch):
    monkeypatch.setattr(
        identity.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=b"credential-sentinel", stderr=b"credential-sentinel"
        ),
    )
    with pytest.raises(RuntimeError) as caught:
        identity.request(default_project_config("test"), ["get", "secret", "test"])
    assert "credential-sentinel" not in str(caught.value)


def test_readiness_of_old_pod_does_not_count_as_activation(monkeypatch):
    config = default_project_config("test")
    ticks = iter([0, 1, 200])
    monkeypatch.setattr(identity.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(identity.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(identity, "get_resource", lambda *_args: _pod())
    with pytest.raises(RuntimeError, match="did not become ready"):
        identity.wait_for_activation(config, previous_uid="old")


def test_helper_failure_attempts_cleanup_and_keeps_pvc(monkeypatch):
    config = default_project_config("test")
    written = []
    monkeypatch.setattr(
        identity,
        "get_resource",
        lambda _cfg, kind, name: next(
            (item for item in written if kind == "pod" and item["kind"] == "Pod"), None
        ),
    )
    monkeypatch.setattr(identity, "write_resource", lambda _cfg, doc, **kwargs: written.append(doc))
    commands = []

    def request(_cfg, args, payload=None):
        commands.append(args)
        if args[0] == "wait":
            raise RuntimeError("helper not ready")
        return b""

    monkeypatch.setattr(identity, "request", request)
    with pytest.raises(RuntimeError, match="helper not ready"):
        identity._seed_volume(config, _pod(), {}, {})
    assert [item["kind"] for item in written] == ["PersistentVolumeClaim", "Pod"]
    assert commands[-1][:2] == ["delete", "pod"]
    assert not any("pvc" in args and "delete" in args for args in commands)


@pytest.mark.parametrize("error", [OSError(), subprocess.TimeoutExpired("kubectl", 180)])
def test_private_command_launch_failures_are_redacted(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(identity.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="deadline"):
        identity._private_command(["kubectl"])


@pytest.mark.parametrize("selected", [b"", b"a\nb", b"a\x00b", b"chosen\n"])
def test_context_is_pinned_once_without_mutating_configuration(monkeypatch, selected):
    config = default_project_config("test")
    monkeypatch.setattr(identity, "_private_command", lambda args: selected)
    if selected == b"chosen\n":
        pinned = identity.pin_context(config)
        assert pinned.kubernetes.kube_context == "chosen"
        assert not config.kubernetes.kube_context
        assert identity.pin_context(pinned) is pinned
    else:
        with pytest.raises(RuntimeError, match="context"):
            identity.pin_context(config)


@pytest.mark.parametrize("content", [b"", b"{}", b"[]", b"not-json"])
def test_resource_lookup_distinguishes_absence_from_invalid_response(monkeypatch, content):
    monkeypatch.setattr(identity, "request", lambda *args: content)
    if content in (b"", b"{}"):
        assert identity.get_resource(default_project_config("test"), "pod", "test") == (
            None if not content else {}
        )
    else:
        with pytest.raises(RuntimeError, match="invalid resource"):
            identity.get_resource(default_project_config("test"), "pod", "test")


@pytest.mark.parametrize("mode", ["valid", "unretained", "empty", "invalid"])
def test_uninstall_requires_recorded_retention_and_prepared_identity(monkeypatch, mode):
    content = {
        "valid": b"kind: Secret\nmetadata:\n  annotations:\n    helm.sh/resource-policy: keep\n",
        "unretained": b"kind: PersistentVolumeClaim\nmetadata: {}\n",
        "empty": b"kind: Service\n",
        "invalid": b"kind: [\n",
    }[mode]
    monkeypatch.setattr(identity, "_private_command", lambda command: content)
    prepared = []
    monkeypatch.setattr(identity, "prepare_identity", lambda config: prepared.append(config))
    config = default_project_config("test")
    if mode == "valid":
        identity.ensure_release_retention(config)
        assert prepared == [config]
    else:
        with pytest.raises(RuntimeError):
            identity.ensure_release_retention(config)
        assert prepared == []


@pytest.mark.parametrize(
    "data",
    [{}, {"unexpected": "opaque"}, {"ssh_host_rsa_key.pub": "opaque"}, {"ssh_host_rsa_key": "!"}],
)
def test_invalid_key_payload_is_rejected_before_rollout(data):
    with pytest.raises(RuntimeError, match="malformed"):
        identity.decode_keys(data)


def test_capture_normalizes_public_material_without_exposing_private_payload(monkeypatch):
    private = base64.b64encode(b"synthetic-test-private").decode()
    monkeypatch.setattr(
        identity, "validated_key", lambda *args: (b"normalized-public\n", "SHA256:ok")
    )
    monkeypatch.setattr(
        identity, "request", lambda *args: json.dumps({"ssh_host_rsa_key": private}).encode()
    )
    keys, fingerprints = identity._capture(default_project_config("test"), _workload(), _pod())
    assert base64.b64decode(keys["ssh_host_rsa_key.pub"]) == b"normalized-public\n"
    assert fingerprints == {"ssh_host_rsa_key": "SHA256:ok"}
    monkeypatch.setattr(identity, "request", lambda *args: b"bad-json")
    with pytest.raises(RuntimeError, match="no rollout"):
        identity._capture(default_project_config("test"), _workload(), _pod())


@pytest.mark.parametrize("existing", [False, True])
def test_identity_seed_is_private_and_cleans_only_its_helper(monkeypatch, existing):
    config = default_project_config("test")
    config.kubernetes.mode = KubernetesMode.HELM
    config.kubernetes.storage_class = "selected-storage"
    written, commands = [], []

    def resource(cfg, kind, name):
        if kind == "pvc" and existing:
            return {"metadata": {"labels": {"sftpwarden.io/identity-for": "test"}}}
        return next((item for item in written if kind == "pod" and item["kind"] == "Pod"), None)

    monkeypatch.setattr(identity, "get_resource", resource)
    monkeypatch.setattr(identity, "write_resource", lambda cfg, doc, **kwargs: written.append(doc))
    monkeypatch.setattr(
        identity, "request", lambda cfg, args, payload=None: commands.append((args, payload))
    )
    identity._seed_volume(config, _pod(), {"private": "synthetic"}, {"public": "SHA256:ok"})
    assert [doc["kind"] for doc in written] == (
        ["Pod"] if existing else ["PersistentVolumeClaim", "Pod"]
    )
    if not existing:
        assert written[0]["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
        assert written[0]["spec"]["storageClassName"] == "selected-storage"
    assert commands[-1][0][:2] == ["delete", "pod"]
    assert json.loads(commands[-2][1])["keys"] == {"private": "synthetic"}
    assert "synthetic" not in json.dumps([args for args, payload in commands])


def test_seed_refuses_unowned_storage_and_reports_cleanup_failure(monkeypatch):
    config = default_project_config("test")
    monkeypatch.setattr(identity, "get_resource", lambda *args: {"metadata": {}})
    with pytest.raises(RuntimeError, match="ownership"):
        identity._seed_volume(config, _pod(), {}, {})
    monkeypatch.setattr(
        identity,
        "get_resource",
        lambda cfg, kind, name: None if kind == "pvc" else (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setattr(identity, "write_resource", lambda *a, **k: None)
    monkeypatch.setattr(identity, "request", lambda *a: b"")
    with pytest.raises(RuntimeError, match="cleanup could not be confirmed"):
        identity._seed_volume(config, _pod(), {}, {})


@pytest.mark.parametrize("problem", ["secret", "replicas", "pod-changed", "legacy-conflict"])
def test_identity_preparation_refuses_ambiguous_authority(monkeypatch, problem):
    config = default_project_config("test")
    workload = _workload()
    resources = {"statefulset": workload, "pod": _pod()}
    if problem == "secret":
        config.kubernetes.host_keys_secret = "missing"
    elif problem == "replicas":
        workload["spec"]["replicas"] = 0
    elif problem == "legacy-conflict":
        workload["spec"]["template"]["spec"]["volumes"].append(
            {"name": "host-key-secret", "secret": {"secretName": "legacy"}}
        )
        resources["secret"] = {"data": {"opaque": "key"}}
    reads = []

    def resource(cfg, kind, name):
        reads.append(kind)
        if problem == "pod-changed" and kind == "pod" and reads.count("pod") == 2:
            return _pod("replacement")
        return resources.get(kind)

    monkeypatch.setattr(identity, "get_resource", resource)
    monkeypatch.setattr(identity, "_capture", lambda *args: ({}, {"key": "SHA256:running"}))
    monkeypatch.setattr(identity, "decode_keys", lambda data: ({}, {"key": "SHA256:conflict"}))
    monkeypatch.setattr(
        identity, "write_resource", lambda *a: pytest.fail("ambiguous identity published")
    )
    with pytest.raises(RuntimeError):
        identity.prepare_identity(config)


@pytest.mark.parametrize("problem", [None, "workload", "configuration", "identity"])
def test_activation_verifies_configuration_and_retained_identity(monkeypatch, problem):
    config = default_project_config("test")
    resources = {
        "pod": _pod("new"),
        "statefulset": _workload(),
        "configmap": {
            "data": {"workloadUID": "workload-uid", "fingerprints": '{"key":"SHA256:ok"}'}
        },
    }
    if problem == "workload":
        resources.pop("statefulset")
    monkeypatch.setattr(identity, "get_resource", lambda cfg, kind, name: resources.get(kind))
    monkeypatch.setattr(
        identity,
        "_capture",
        lambda *a: ({}, {"key": "changed" if problem == "identity" else "SHA256:ok"}),
    )
    verified = []

    def request(cfg, args):
        verified.append(args)
        if problem == "configuration":
            raise RuntimeError("unverified")

    monkeypatch.setattr(identity, "request", request)
    if problem:
        with pytest.raises(RuntimeError):
            identity.wait_for_activation(config, previous_uid="old")
    else:
        identity.wait_for_activation(config, previous_uid="old")
        assert verified[0][:4] == ["exec", "test-0", "-c", "sftpwarden"]


@pytest.mark.parametrize("managed", [False, True])
def test_existing_provider_secret_requires_ownership_and_retains_metadata(monkeypatch, managed):
    config = default_project_config("test", ProviderType.POSTGRESQL, dsn="postgresql://db/test")
    labels = {"sftpwarden.io/provider-secret": "test"} if managed else {}
    existing = {
        "metadata": {
            "labels": labels,
            "resourceVersion": "42",
            "annotations": {
                "operator": "keep",
                "kubectl.kubernetes.io/last-applied-configuration": "private",
            },
        }
    }
    monkeypatch.setattr(identity, "get_resource", lambda *args: existing)
    written = []
    monkeypatch.setattr(
        identity, "write_resource", lambda cfg, doc, **kw: written.append((doc, kw))
    )
    if managed:
        identity.prepare_provider_secret(config)
        assert written[0][1] == {"action": "replace"}
        assert written[0][0]["metadata"]["annotations"] == {"operator": "keep"}
        assert written[0][0]["metadata"]["resourceVersion"] == "42"
    else:
        with pytest.raises(RuntimeError, match="outside"):
            identity.prepare_provider_secret(config)
        assert not written


@pytest.mark.parametrize("present", [False, True])
def test_operator_provider_secret_must_contain_dsn_and_is_never_changed(monkeypatch, present):
    config = default_project_config("test", ProviderType.POSTGRESQL, dsn="postgresql://db/test")
    config.kubernetes.provider_dsn_secret = "operator"
    monkeypatch.setattr(
        identity,
        "get_resource",
        lambda *a: {"data": {identity.PROVIDER_DSN_ENV: "opaque"}} if present else {},
    )
    monkeypatch.setattr(
        identity, "write_resource", lambda *a, **k: pytest.fail("operator secret modified")
    )
    if present:
        identity.prepare_provider_secret(config)
    else:
        with pytest.raises(RuntimeError, match="missing"):
            identity.prepare_provider_secret(config)


def test_no_workload_needs_no_identity_capture_and_file_provider_needs_no_secret(monkeypatch):
    config = default_project_config("test")
    monkeypatch.setattr(identity, "get_resource", lambda *a: None)
    monkeypatch.setattr(identity, "_capture", lambda *a: pytest.fail("no workload to capture"))
    identity.prepare_identity(config)
    monkeypatch.setattr(
        identity, "get_resource", lambda *a: pytest.fail("file provider queried a Secret")
    )
    identity.prepare_provider_secret(config)


def test_legacy_provider_secret_adoption_requires_matching_workload_reference(monkeypatch):
    config = default_project_config("test", ProviderType.POSTGRESQL, dsn="postgresql://db/test")
    secret = {
        "metadata": {
            "labels": {
                "app.kubernetes.io/managed-by": "sftpwarden",
                "app.kubernetes.io/instance": "test",
            }
        }
    }
    workload = _workload()
    workload["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {
            "name": identity.PROVIDER_DSN_ENV,
            "valueFrom": {"secretKeyRef": {"name": "test-provider"}},
        }
    ]
    monkeypatch.setattr(
        identity, "get_resource", lambda cfg, kind, name: secret if kind == "secret" else workload
    )
    written = []
    monkeypatch.setattr(identity, "write_resource", lambda cfg, doc, **kw: written.append(doc))
    identity.prepare_provider_secret(config)
    assert written[0]["metadata"]["labels"]["sftpwarden.io/provider-secret"] == "test"
    config.provider.dsn = None
    with pytest.raises(RuntimeError, match="DSN is missing"):
        identity.prepare_provider_secret(config)
