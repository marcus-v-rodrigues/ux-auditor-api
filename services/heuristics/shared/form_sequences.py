"""
Detecção compartilhada de sequências de formulário.

As funções deste módulo retornam estruturas neutras para que os módulos de
evidence/compression possam transformá-las em `HeuristicMatch` com categorias
distintas.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.heuristics.base import (
    action_group,
    action_kind,
    action_target,
    action_timestamp,
    action_value_signature,
    get_config,
    ordered_actions,
    unique_preserve,
)
from services.heuristics.types import HeuristicContext

INPUT_KINDS = {"input", "radio", "checkbox", "select", "toggle"}
NUMERIC_SUFFIX_RE = re.compile(r"(\d+)$")


def iter_form_actions(ctx: HeuristicContext) -> List[Any]:
    return [item for item in ordered_actions(ctx) if action_kind(item) in INPUT_KINDS]


def detect_sequential_form_sequences(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = iter_form_actions(ctx)
    if len(actions) < 2:
        return []

    max_gap_ms = int(get_config(ctx, "sequential_filling_max_gap_ms", 2500))
    sequences: List[Dict[str, Any]] = []
    current: List[Any] = [actions[0]]

    for action in actions[1:]:
        prev = current[-1]
        same_group = action_group(action) and action_group(action) == action_group(prev)
        same_target = action_target(action) and action_target(action) == action_target(prev)
        gap = action_timestamp(action) - action_timestamp(prev)
        if (same_group and gap <= max_gap_ms) or same_target:
            current.append(action)
            continue
        if len(current) >= 2 and len({action_target(item) or action_group(item) for item in current}) >= 2:
            sequences.append(
                {
                    "start_ts": action_timestamp(current[0]),
                    "end_ts": action_timestamp(current[-1]),
                    "target_ref": action_group(current[-1]) or action_target(current[-1]),
                    "target_refs": unique_preserve(
                        [action_target(item) or action_group(item) for item in current if action_target(item) or action_group(item)]
                    ),
                    "action_count": len(current),
                    "gap_ms": max(
                        action_timestamp(current[idx + 1]) - action_timestamp(current[idx])
                        for idx in range(len(current) - 1)
                    ),
                    "group": action_group(current[-1]),
                }
            )
        current = [action]

    if len(current) >= 2 and len({action_target(item) or action_group(item) for item in current}) >= 2:
        sequences.append(
            {
                "start_ts": action_timestamp(current[0]),
                "end_ts": action_timestamp(current[-1]),
                "target_ref": action_group(current[-1]) or action_target(current[-1]),
                "target_refs": unique_preserve(
                    [action_target(item) or action_group(item) for item in current if action_target(item) or action_group(item)]
                ),
                "action_count": len(current),
                "gap_ms": max(
                    action_timestamp(current[idx + 1]) - action_timestamp(current[idx])
                    for idx in range(len(current) - 1)
                ),
                "group": action_group(current[-1]),
            }
        )

    return sequences


def detect_out_of_order_sequences(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = iter_form_actions(ctx)
    if len(actions) < 3:
        return []

    numbered: List[Dict[str, Any]] = []
    for action in actions:
        target = action_target(action) or action_group(action) or ""
        match = NUMERIC_SUFFIX_RE.search(target)
        if match:
            numbered.append(
                {
                    "action": action,
                    "number": int(match.group(1)),
                }
            )

    if len(numbered) < 3:
        return []

    sequence = [item["number"] for item in numbered]
    if sequence == sorted(sequence):
        return []

    return [
        {
            "start_ts": action_timestamp(numbered[0]["action"]),
            "end_ts": action_timestamp(numbered[-1]["action"]),
            "target_ref": action_target(numbered[-1]["action"]) or action_group(numbered[-1]["action"]),
            "target_refs": unique_preserve(
                [
                    action_target(item["action"]) or action_group(item["action"])
                    for item in numbered
                    if action_target(item["action"]) or action_group(item["action"])
                ]
            ),
            "numeric_sequence": sequence[:20],
            "count": len(numbered),
        }
    ]


def detect_value_revision_points(ctx: HeuristicContext) -> List[Dict[str, Any]]:
    actions = iter_form_actions(ctx)
    revisions: List[Dict[str, Any]] = []
    last_by_key: Dict[str, Any] = {}
    for action in actions:
        key = action_target(action) or action_group(action)
        if not key:
            continue
        current_signature = action_value_signature(action)
        previous = last_by_key.get(key)
        if previous is not None and action_value_signature(previous) != current_signature:
            revisions.append(
                {
                    "start_ts": action_timestamp(previous),
                    "end_ts": action_timestamp(action),
                    "target_ref": key,
                    "previous_value": action_value_signature(previous),
                    "current_value": current_signature,
                    "target_group": action_group(action),
                }
            )
        last_by_key[key] = action
    return revisions
