"""Parallel release gates must preserve complete, non-overlapping acceptance coverage."""

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

module = runpy.run_path("tools/run_release_gates.py")
group_for = module["group_for"]
Selection = module["Selection"]


def test_independent_contracts_are_selected_once_and_platform_gates_stay_external():
    assert group_for(Path("test_cli_surface.py"), {"release_validation"}) == "contracts"
    assert group_for(Path("test_bounded_load.py"), {"release_external"}) == "load"
    for marker in ("release_kubernetes", "release_helm"):
        assert group_for(Path("future_cluster.py"), {marker, "release_external"}) == "cluster"
    for group, files in module["EXTERNAL_GROUPS"].items():
        for path in files:
            assert group_for(Path(path), {"release_external"}) == group


def test_unassigned_external_test_blocks_the_release_instead_of_disappearing():
    with pytest.raises(ValueError, match="exactly one"):
        group_for(Path("test_new_external_feature.py"), {"release_external"})


def test_collection_partition_preserves_each_case_and_rejects_empty_selection():
    def item(path, markers):
        return SimpleNamespace(
            path=Path(path), iter_markers=lambda: [SimpleNamespace(name=name) for name in markers]
        )

    contract = item("test_cli_surface.py", {"release_validation"})
    external = item("test_auth_policy.py", {"release_external"})
    dropped = []
    config = SimpleNamespace(
        pluginmanager=SimpleNamespace(getplugin=lambda _: None),
        hook=SimpleNamespace(pytest_deselected=lambda items: dropped.extend(items)),
    )
    items = [contract, external]
    Selection("policy").pytest_collection_modifyitems(config, items)
    assert items == [external]
    assert dropped == [contract]
    with pytest.raises(pytest.UsageError, match="No release cases"):
        Selection("load").pytest_collection_modifyitems(config, items)
    with pytest.raises(pytest.UsageError, match="Assign external"):
        Selection("policy").pytest_collection_modifyitems(
            config, [item("unknown.py", {"release_external"})]
        )
