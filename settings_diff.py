from __future__ import annotations

from dataclasses import dataclass, field, fields

from settings_models import GlobalSettings, Settings

CUSTOMER_ATTRS = ("balance_below", "margin_below", "margin_above", "margin_deadband", "billed_above")


@dataclass
class SettingsDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    customer_changes: dict[str, list[str]] = field(default_factory=dict)
    global_changes: list[str] = field(default_factory=list)


def diff_settings(old: Settings, new: Settings) -> SettingsDiff:
    old_ids = set(old.customer_ids)
    new_ids = set(new.customer_ids)

    customer_changes: dict[str, list[str]] = {}
    for cid in sorted(new_ids & old_ids):
        old_w = old.get_watch(cid)
        new_w = new.get_watch(cid)
        changes: list[str] = []
        for attr in CUSTOMER_ATTRS:
            old_val = getattr(old_w, attr, None)
            new_val = getattr(new_w, attr, None)
            if old_val != new_val:
                changes.append(f"{attr}: {old_val} -> {new_val}")
        if changes:
            customer_changes[cid] = changes

    global_changes: list[str] = []
    for f in fields(GlobalSettings):
        old_val = getattr(old.global_, f.name)
        new_val = getattr(new.global_, f.name)
        if old_val != new_val:
            global_changes.append(f"{f.name}: {old_val} -> {new_val}")

    return SettingsDiff(
        added=sorted(new_ids - old_ids),
        removed=sorted(old_ids - new_ids),
        customer_changes=customer_changes,
        global_changes=global_changes,
    )
