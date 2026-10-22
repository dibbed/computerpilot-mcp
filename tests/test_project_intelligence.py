from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any, cast

import pytest

from core.errors import ToolError
from core.resource_locks import canonical_path
from tools.language.requests import validate_request


def hierarchy_item(path: Path, name: str = "alpha") -> dict[str, Any]:
    return {
        "uri": path.as_uri(), "name": name, "kind": 12,
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}},
        "selectionRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}},
        "data": {"opaque": [1, "provider-token"]},
    }


@pytest.mark.parametrize("method", [
    "callHierarchy/incomingCalls", "callHierarchy/outgoingCalls", "typeHierarchy/supertypes", "typeHierarchy/subtypes",
])
def test_fixed_hierarchy_items_are_validated(tmp_path: Path, method: str) -> None:
    target = tmp_path / "a.py"
    target.write_text("alpha\n", encoding="utf-8")
    params = {"item": hierarchy_item(target)}
    assert validate_request(tmp_path, method, params) == params


@pytest.mark.parametrize("case", ["outside", "range", "surrogate", "selection", "extra", "kind", "data"])
def test_hierarchy_item_denials(tmp_path: Path, case: str) -> None:
    target = tmp_path / "a.py"
    target.write_text("a😀bc\n", encoding="utf-8")
    item = hierarchy_item(target)
    if case == "outside":
        item["uri"] = (tmp_path.parent / "outside.py").as_uri()
    elif case == "range":
        item["range"]["end"]["character"] = 99
    elif case == "surrogate":
        item["selectionRange"]["end"]["character"] = 2
    elif case == "selection":
        item["selectionRange"]["end"]["line"] = 1
    elif case == "extra":
        item["command"] = "do-not-dispatch"
    elif case == "kind":
        item["kind"] = True
    else:
        item["data"] = "x" * 65_536
    with pytest.raises(ToolError) as caught:
        validate_request(tmp_path, "callHierarchy/incomingCalls", {"item": item})
    assert caught.value.code in {"lsp_params_invalid", "lsp_position_invalid", "lsp_path_outside_workspace"}


def test_prepare_type_hierarchy_is_fixed_position_request(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("alpha\n", encoding="utf-8")
    params = {"textDocument": {"uri": target.as_uri()}, "position": {"line": 0, "character": 0}}
    assert validate_request(tmp_path, "textDocument/prepareTypeHierarchy", params) == params
    with pytest.raises(ToolError, match="not supported"):
        validate_request(tmp_path, "workspace/executeCommand", {})


class SemanticManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.capabilities = frozenset({
            "workspaceSymbolProvider", "documentSymbolProvider", "definitionProvider", "referencesProvider",
            "callHierarchyProvider", "typeHierarchyProvider",
        })
        self.incarnation = "one"
        self.responses: dict[str, Any] = {}
        self.hook: Any = None
        for name in ("alpha", "bravo"):
            (root / f"{name}.ts").write_bytes((name + "\n").encode("utf-8"))
        self.alpha = hierarchy_item(root / "alpha.ts")
        self.bravo = hierarchy_item(root / "bravo.ts", "bravo")
        self.responses = {
            "workspace/symbol": [self.symbol(self.alpha)],
            "textDocument/documentSymbol": [{k: v for k, v in self.alpha.items() if k not in {"uri", "data"}}],
            "textDocument/definition": {"uri": self.alpha["uri"], "range": self.alpha["selectionRange"]},
            "textDocument/references": [], "textDocument/prepareCallHierarchy": [self.alpha],
            "callHierarchy/incomingCalls": [], "callHierarchy/outgoingCalls": [],
            "textDocument/prepareTypeHierarchy": [], "typeHierarchy/supertypes": [], "typeHierarchy/subtypes": [],
        }

    @staticmethod
    def symbol(item: dict[str, Any]) -> dict[str, Any]:
        return {"name": item["name"], "kind": item["kind"], "location": {"uri": item["uri"], "range": item["range"]}}

    async def arequest_with_evidence(
        self, root: Path, language_id: str, method: str, params: dict[str, Any], *, timeout_sec: float,
    ) -> Any:
        from tools.language.providers import SessionKey
        from tools.language.sessions import SessionReply

        assert root == self.root and language_id == "typescript"
        validate_request(root, method, params)
        self.calls.append((method, params, timeout_sec))
        if self.hook:
            self.hook(method)
        value = self.responses[method]
        if callable(value):
            value = value(params)
        docs = tuple((path.as_uri(), 1, hashlib.sha256(path.read_bytes()).hexdigest()) for path in root.glob("*.ts"))
        key = SessionKey(canonical_path(root), "a" * 64, language_id, "fixture", "b" * 64, "generation")
        return SessionReply(value, key, self.incarnation, self.capabilities, docs)


def semantic_context(manager: SemanticManager, *, symbol: str = "alpha", **kwargs: Any) -> Any:
    from tools.language.sessions import LspSessionManager
    from tools.project.intelligence import lookup_semantic_context

    options: dict[str, Any] = dict(
        language_id="typescript", anchor=None, max_depth=2, max_relationships=1000, offset=0, deadline=time.monotonic() + 5,
    )
    options.update(kwargs)
    return asyncio.run(lookup_semantic_context(cast(LspSessionManager, manager), manager.root, symbol, **options))


def test_ambiguous_symbol_requires_anchor(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    other = hierarchy_item(tmp_path / "bravo.ts")
    manager.responses["workspace/symbol"] = [manager.symbol(manager.alpha), manager.symbol(other)]
    result = semantic_context(manager)
    assert result.fallback_reason == "ambiguous_symbol" and len(result.definitions) == 2
    assert [call[0] for call in manager.calls] == ["workspace/symbol"]
    assert not result.edges and not result.complete


def test_anchor_must_match_symbol(tmp_path: Path) -> None:
    from tools.project.intelligence import CodePosition

    manager = SemanticManager(tmp_path)
    result = semantic_context(manager, anchor=CodePosition(path="alpha.ts", line=0, character=1), max_depth=0)
    assert result.definitions[0].qualified_name == "alpha"
    assert manager.calls[0][0] == "textDocument/documentSymbol"
    assert all(call[0] != "workspace/symbol" for call in manager.calls)
    manager.responses["textDocument/documentSymbol"] = [{k: v for k, v in manager.bravo.items() if k not in {"uri", "data"}}]
    result = semantic_context(manager, anchor=CodePosition(path="alpha.ts", line=0, character=1))
    assert result.fallback_reason == "not_found" and not result.edges


def test_location_link_and_reference_provenance(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["textDocument/definition"] = [{
        "targetUri": manager.alpha["uri"], "targetRange": manager.alpha["range"],
        "targetSelectionRange": manager.alpha["selectionRange"],
    }]
    manager.responses["textDocument/references"] = [{"uri": manager.bravo["uri"], "range": manager.bravo["range"]}]
    result = semantic_context(manager, max_depth=1)
    references = [edge for edge in result.edges if edge.kind == "reference"]
    assert len(references) == 1 and references[0].to_symbol.relative_uri == "bravo.ts"
    assert references[0].confidence == "semantic_reported" and references[0].evidence.document_version == 1
    assert references[0].to_symbol.content_digest == hashlib.sha256(b"bravo\n").hexdigest()
    request = next(params for method, params, _ in manager.calls if method == "textDocument/references")
    assert request["context"] == {"includeDeclaration": False}
    assert not result.completeness_by_relation["import"] and not result.complete


def test_call_cycle_has_bounded_unique_edges(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["callHierarchy/outgoingCalls"] = lambda p: [{
        "to": manager.bravo if p["item"]["name"] == "alpha" else manager.alpha,
        "fromRanges": [manager.alpha["range"]],
    }]
    result = semantic_context(manager)
    edges = [edge for edge in result.edges if edge.kind == "callee"]
    assert len(edges) == 2 and {edge.depth for edge in edges} == {1, 2}
    assert len({(edge.from_symbol, edge.to_symbol) for edge in edges}) == 2
    assert len([call for call in manager.calls if call[0] == "callHierarchy/outgoingCalls"]) == 2
    page1 = semantic_context(manager, max_relationships=1)
    page2 = semantic_context(manager, max_relationships=1, offset=1)
    assert page1.total == page2.total == len(result.edges)
    assert page1.edges != page2.edges and page1.truncated


def test_type_hierarchy_capability_absent_is_incomplete(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.capabilities -= {"typeHierarchyProvider"}
    result = semantic_context(manager)
    assert not result.completeness_by_relation["inheritance"]
    assert all("typeHierarchy" not in call[0] and "TypeHierarchy" not in call[0] for call in manager.calls)
    assert "capability_missing:typeHierarchyProvider" in result.missing_inputs


def test_provider_uri_outside_root_denied(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["textDocument/definition"] = {
        "uri": (tmp_path.parent / "private-canary.ts").as_uri(), "range": manager.alpha["range"],
    }
    result = semantic_context(manager)
    assert not result.edges and not result.complete and result.fallback_reason == "lsp_path_outside_workspace"
    assert "private-canary" not in result.model_dump_json()


def test_generation_change_discards_batch(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.hook = lambda method: setattr(manager, "incarnation", "two") if method == "textDocument/references" else None
    result = semantic_context(manager)
    assert not result.edges and not result.definitions and result.fallback_reason == "lsp_snapshot_changed"
    assert manager.calls[-1][0] == "textDocument/references"


def test_source_change_discards_batch(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    def change(method: str) -> None:
        if method == "textDocument/references":
            (tmp_path / "alpha.ts").write_text("other\n", encoding="utf-8")

    manager.hook = change
    result = semantic_context(manager)
    assert not result.edges and not result.definitions and result.fallback_reason == "lsp_snapshot_changed"


def test_semantic_deadline_stops_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.project import intelligence

    manager = SemanticManager(tmp_path)
    clock = [0.0]
    manager.hook = lambda _method: clock.__setitem__(0, clock[0] + 1)
    monkeypatch.setattr(intelligence.time, "monotonic", lambda: clock[0])
    result = semantic_context(manager, deadline=0.5)
    assert len(manager.calls) == 1 and manager.calls[0][2] <= 0.5
    assert result.fallback_reason == "lsp_timeout" and not result.edges


def test_hierarchy_fanout_and_depth_never_exceed_bounds(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["callHierarchy/outgoingCalls"] = [{"to": manager.bravo, "fromRanges": []}] * 101
    result = semantic_context(manager, max_depth=1)
    assert not result.total_exact and "fanout_limit" in result.missing_inputs
    assert len([call for call in manager.calls if call[0] == "callHierarchy/outgoingCalls"]) == 1
    assert len(result.edges) <= 1000 and all(edge.depth <= 1 for edge in result.edges)


def test_same_location_distinct_containers_remain_ambiguous(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["workspace/symbol"] = [
        {**manager.symbol(manager.alpha), "containerName": "First"},
        {**manager.symbol(manager.alpha), "containerName": "Second"},
    ]
    result = semantic_context(manager)
    assert result.fallback_reason == "ambiguous_symbol"
    assert {key.qualified_name for key in result.definitions} == {"First.alpha", "Second.alpha"}
    assert len(manager.calls) == 1


def test_prepared_hierarchy_cannot_change_selected_symbol(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["textDocument/prepareCallHierarchy"] = [manager.bravo]
    manager.responses["callHierarchy/outgoingCalls"] = [{"to": manager.alpha, "fromRanges": []}]
    result = semantic_context(manager)
    assert result.fallback_reason == "lsp_symbol_mismatch" and not result.edges
    assert all(not call[0].startswith("callHierarchy/") for call in manager.calls)


def test_missing_selection_capability_reports_reason(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.capabilities -= {"workspaceSymbolProvider"}
    result = semantic_context(manager)
    assert result.fallback_reason == "capability_missing:workspaceSymbolProvider"
    assert len(manager.calls) == 1


def test_cancelled_read_is_not_replayed(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)

    def cancel(_method: str) -> None:
        raise asyncio.CancelledError

    manager.hook = cancel
    with pytest.raises(asyncio.CancelledError):
        semantic_context(manager)
    assert len(manager.calls) == 1


def test_symbol_identity_does_not_depend_on_query_spelling(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["workspace/symbol"] = [{**manager.symbol(manager.alpha), "containerName": "Container"}]
    short = semantic_context(manager)
    qualified = semantic_context(manager, symbol="Container.alpha")
    assert short.definitions == qualified.definitions
    assert short.definitions[0].qualified_name == "Container.alpha"


@pytest.mark.parametrize("from_ranges", [None, "missing", {"start": 0}])
def test_call_edges_require_valid_from_ranges(tmp_path: Path, from_ranges: Any) -> None:
    manager = SemanticManager(tmp_path)
    row = {"to": manager.bravo}
    if from_ranges != "missing":
        row["fromRanges"] = from_ranges
    manager.responses["callHierarchy/outgoingCalls"] = [row]
    result = semantic_context(manager)
    assert result.fallback_reason == "lsp_result_invalid" and not result.edges


def test_definition_selection_normalizes_coarse_workspace_location(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    (tmp_path / "alpha.ts").write_bytes(b"func alpha() {}\n")
    manager.alpha["range"]["end"]["character"] = 15
    manager.alpha["selectionRange"] = {"start": {"line": 0, "character": 5}, "end": {"line": 0, "character": 10}}
    manager.responses["workspace/symbol"] = [manager.symbol(manager.alpha)]
    manager.responses["textDocument/definition"] = {
        "targetUri": manager.alpha["uri"], "targetRange": manager.alpha["range"],
        "targetSelectionRange": manager.alpha["selectionRange"],
    }
    result = semantic_context(manager)
    assert result.fallback_reason is None and result.definitions[0].start_character == 5
    prepare = next(params for method, params, _ in manager.calls if method == "textDocument/prepareCallHierarchy")
    assert prepare["position"] == {"line": 0, "character": 5}


def test_hierarchy_selection_normalizes_without_definition_provider(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.capabilities -= {"definitionProvider"}
    (tmp_path / "alpha.ts").write_bytes(b"func alpha() {}\n")
    manager.alpha["range"]["end"]["character"] = 15
    manager.alpha["selectionRange"] = {"start": {"line": 0, "character": 5}, "end": {"line": 0, "character": 10}}
    manager.responses["workspace/symbol"] = [manager.symbol(manager.alpha)]
    result = semantic_context(manager)
    assert result.fallback_reason is None and result.definitions[0].start_character == 5
    reference = next(params for method, params, _ in manager.calls if method == "textDocument/references")
    assert reference["position"] == {"line": 0, "character": 5}
    assert not result.completeness_by_relation["definition"]


def test_ambiguous_definitions_do_not_guess_hierarchy_root(tmp_path: Path) -> None:
    manager = SemanticManager(tmp_path)
    manager.responses["textDocument/definition"] = [
        {"uri": item["uri"], "range": item["range"]} for item in (manager.alpha, manager.bravo)
    ]
    result = semantic_context(manager)
    assert result.fallback_reason == "ambiguous_symbol" and not result.edges
    assert all("Hierarchy" not in call[0] for call in manager.calls)
