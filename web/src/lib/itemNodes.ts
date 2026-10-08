import type { ItemFilter } from "./types";

/** A whole category, group or "Ungrouped" bucket of the tree, described as a filter so every
 *  bulk action can run on it without ticking its rows. */
export interface TreeNodeSel {
  key: string;
  label: string;
  filter: ItemFilter;
  count: number;
  /** set on a group node: lets the group page and the tree share the same node */
  groupId?: string;
}

/** What a tree node's menu can start. The first block needs the catalog module. */
export type NodeAction =
  | "catalog"
  | "share"
  | "labels"
  | "tally"
  | "availability"
  | "price"
  | "fields"
  | "move"
  | "rename"
  | "archive"
  | "export";

export const categoryNode = (id: string | null, name: string, count: number): TreeNodeSel => ({
  key: `cat:${id ?? "none"}`,
  label: name,
  filter: id ? { category_id: id } : { uncategorised: true },
  count,
});

export const looseNode = (id: string | null, name: string, count: number): TreeNodeSel => ({
  key: `loose:${id ?? "none"}`,
  label: `${name} · ungrouped`,
  filter: id ? { category_id: id, ungrouped: true } : { uncategorised: true, ungrouped: true },
  count,
});

export const groupNode = (id: string, name: string, count: number): TreeNodeSel => ({
  key: `grp:${id}`,
  label: name,
  filter: { group_id: id },
  count,
  groupId: id,
});
