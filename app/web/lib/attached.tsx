"use client";
import { createContext, useContext } from "react";

/**
 * Which map states are attached to the chat, read by `StateCard` directly.
 *
 * **Why a context and not node data.** `MapPane` has no `onNodesChange`, so
 * xyflow re-syncs from the `nodes` prop whenever that array's identity changes
 * — and `MapPane` rebuilds it by running `layout()` from scratch. Putting
 * `attached` on a node's `data` therefore means every click on a state resets
 * the position of every state anyone has dragged. The comment in `MapPane`
 * about not reassigning an unchanged payload is guarding the same edge.
 *
 * A context leaves the node set untouched: attaching re-renders the cards and
 * nothing else.
 */
export const AttachedStates = createContext<ReadonlySet<string>>(new Set());

export const useAttached = (key: string) => useContext(AttachedStates).has(key);
