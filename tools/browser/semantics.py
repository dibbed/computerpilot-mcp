"""Bounded semantic browser snapshots and query helpers."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

_SEMANTIC_SNAPSHOT_JS = r"""
() => {
  const implicitRole = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.trim().toLowerCase();
    const tag = el.tagName.toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "summary") return "button";
    if (tag === "img") return "img";
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "range") return "slider";
      if (type === "number") return "spinbutton";
      if (type !== "hidden") return "textbox";
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    return null;
  };

  const associatedLabel = (el) => {
    if (el.id) {
      try {
        const label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (label) return (label.innerText || label.textContent || "").trim();
      } catch (_) {}
    }
    const parent = el.closest("label");
    return parent ? (parent.innerText || parent.textContent || "").trim() : "";
  };

  const accessibleName = (el) => {
    const aria = (el.getAttribute("aria-label") || "").trim();
    if (aria) return aria;
    const labelledBy = (el.getAttribute("aria-labelledby") || "").trim();
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => (node.innerText || node.textContent || "").trim())
        .filter(Boolean)
        .join(" ");
      if (text) return text;
    }
    const label = associatedLabel(el);
    if (label) return label;
    const alt = (el.getAttribute("alt") || "").trim();
    if (alt) return alt;
    const title = (el.getAttribute("title") || "").trim();
    if (title) return title;
    const placeholder = (el.getAttribute("placeholder") || "").trim();
    if (placeholder) return placeholder;
    if (el instanceof HTMLInputElement) {
      const type = (el.type || "").toLowerCase();
      if (["button", "submit", "reset"].includes(type) && el.value) return el.value.trim();
    }
    return (el.innerText || el.textContent || "").trim();
  };

  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (el.hidden || el.getAttribute("aria-hidden") === "true") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const selector = [
    "button", "a[href]", "input:not([type=hidden])", "select", "textarea",
    "summary", "[role]", "[aria-label]", "[aria-labelledby]", "[data-testid]",
    "[contenteditable=true]", "h1", "h2", "h3", "h4", "h5", "h6"
  ].join(",");

  return Array.from(document.querySelectorAll(selector))
    .filter(isVisible)
    .map((el) => {
      const role = implicitRole(el);
      const name = accessibleName(el);
      const text = (el.innerText || el.textContent || "").trim();
      const input = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement;
      return {
        role,
        name,
        label: associatedLabel(el) || null,
        text,
        test_id: el.getAttribute("data-testid"),
        tag: el.tagName.toLowerCase(),
        disabled: Boolean(el.disabled) || el.getAttribute("aria-disabled") === "true",
        selected: el.getAttribute("aria-selected") === "true" || Boolean(el.selected),
        expanded: el.hasAttribute("aria-expanded") ? el.getAttribute("aria-expanded") === "true" : null,
        checked: el.hasAttribute("aria-checked")
          ? el.getAttribute("aria-checked") === "true"
          : (("checked" in el) ? Boolean(el.checked) : null),
        value: input ? String(el.value || "") : null
      };
    });
}
"""


def _clean_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) > limit:
        return text[:limit]
    return text


def _target_key(node: dict[str, Any]) -> tuple[str | None, ...]:
    return (
        node.get("role"),
        node.get("name"),
        node.get("label"),
        node.get("text"),
        node.get("test_id"),
        node.get("tag"),
    )


async def build_snapshot(
    page: Any,
    *,
    generation: int,
    page_id: str,
    max_nodes: int,
    max_text_chars: int,
) -> dict[str, Any]:
    """Collect a bounded semantic representation of the active page."""

    max_nodes = max(1, max_nodes)
    max_text_chars = max(1, max_text_chars)
    raw = await page.evaluate(_SEMANTIC_SNAPSHOT_JS)
    if not isinstance(raw, list):
        raw = []

    nodes: list[dict[str, Any]] = []
    total_text_chars = 0
    ordinals: defaultdict[tuple[str | None, ...], int] = defaultdict(int)
    truncated = False

    for item in raw:
        if len(nodes) >= max_nodes:
            truncated = True
            break
        if not isinstance(item, dict):
            continue

        node: dict[str, Any] = {
            "ref": f"n{len(nodes) + 1}",
            "role": _clean_text(item.get("role"), 100),
            "name": _clean_text(item.get("name"), 2_000),
            "label": _clean_text(item.get("label"), 2_000),
            "text": _clean_text(item.get("text"), 4_000),
            "test_id": _clean_text(item.get("test_id"), 500),
            "tag": _clean_text(item.get("tag"), 100),
            "disabled": bool(item.get("disabled", False)),
            "selected": bool(item.get("selected", False)),
            "expanded": item.get("expanded") if isinstance(item.get("expanded"), bool) else None,
            "checked": item.get("checked") if isinstance(item.get("checked"), bool) else None,
            "value": _clean_text(item.get("value"), 2_000),
        }
        text_cost = sum(len(value) for value in node.values() if isinstance(value, str))
        if nodes and total_text_chars + text_cost > max_text_chars:
            truncated = True
            break
        if not nodes and text_cost > max_text_chars:
            remaining = max_text_chars
            for field in ("name", "label", "text", "value"):
                value = node.get(field)
                if not isinstance(value, str):
                    continue
                node[field] = value[:remaining]
                remaining = max(0, remaining - len(node[field]))
            truncated = True
            text_cost = sum(len(value) for value in node.values() if isinstance(value, str))

        key = _target_key(node)
        node["ordinal"] = ordinals[key]
        ordinals[key] += 1
        nodes.append(node)
        total_text_chars += text_cost

    if len(raw) > len(nodes):
        truncated = True

    title = await page.title()
    return {
        "ok": True,
        "page_id": page_id,
        "url": str(getattr(page, "url", "")),
        "title": str(title),
        "generation": generation,
        "nodes": nodes,
        "node_count": len(nodes),
        "source_node_count": len(raw),
        "text_chars": total_text_chars,
        "truncated": truncated,
    }


def _trim_json_value(value: Any, max_chars: int) -> Any:
    if max_chars <= 0:
        return "" if isinstance(value, str) else value
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, list):
        return [_trim_json_value(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {str(key): _trim_json_value(item, max_chars) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_chars]


def bound_records(
    records: list[Any],
    *,
    max_items: int,
    max_text_chars: int,
) -> dict[str, Any]:
    """Bound structured extraction records by count and serialized text size."""

    limit_items = max(1, max_items)
    limit_chars = max(1, max_text_chars)
    items: list[Any] = []
    used_chars = 0
    truncated = False

    for record in records:
        if len(items) >= limit_items:
            truncated = True
            break
        normalized = _trim_json_value(record, limit_chars)
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), default=str)
        if items and used_chars + len(encoded) > limit_chars:
            truncated = True
            break
        if not items and len(encoded) > limit_chars:
            normalized = _trim_json_value(record, max(1, limit_chars // 2))
            encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), default=str)
            if len(encoded) > limit_chars:
                normalized = {"value": encoded[: max(1, limit_chars - 20)]}
                encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            truncated = True
        items.append(normalized)
        used_chars += len(encoded)

    if len(records) > len(items):
        truncated = True
    return {
        "items": items,
        "count": len(items),
        "total_count": len(records),
        "text_chars": used_chars,
        "truncated": truncated,
    }


def query_nodes(
    nodes: list[dict[str, Any]],
    *,
    role: str | None = None,
    name: str | None = None,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    """Filter semantic nodes by exact case-insensitive semantic fields."""

    filters = {
        "role": role,
        "name": name,
        "label": label,
        "text": text,
        "test_id": test_id,
    }
    active = {key: value.casefold() for key, value in filters.items() if value is not None}
    if not active:
        raise ValueError("At least one semantic query field is required.")

    matches: list[dict[str, Any]] = []
    total_matches = 0
    for node in nodes:
        if all(str(node.get(key) or "").casefold() == expected for key, expected in active.items()):
            total_matches += 1
            if len(matches) < max(1, max_results):
                matches.append(node)

    return {
        "matches": matches,
        "count": total_matches,
        "returned": len(matches),
        "unique": total_matches == 1,
        "truncated": total_matches > len(matches),
    }
