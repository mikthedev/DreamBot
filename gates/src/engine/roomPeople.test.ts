import { describe, expect, it } from "vitest";
import { foldRoom, mergePending, mergePerson, mergeWire, patchRoom, remapStateIds, sameHost } from "../../api/roomPeople.js";
import { signalForMe, uniqueIds } from "../ui/net";

describe("room people and turns", () => {
  it("keeps a Discord name when the host later publishes Guest", () => {
    const merged = mergePerson({ id: "1", name: "Alex", avatar: "a" }, { id: "1", name: "Guest", avatar: null });
    expect(merged.name).toBe("Alex");
    expect(merged.avatar).toBe("a");
  });

  it("does not let a guest overwrite a seat-* host table", () => {
    expect(sameHost("seat-abc", "999", "")).toBe(false);
    expect(sameHost("seat-abc", "999", "seat-abc")).toBe(true);
    expect(sameHost("999", "999", "seat-abc")).toBe(true);
  });

  it("does not drop a later pending action when the host acks an older one", () => {
    const pending = mergePending(
      [
        { nid: "a", from: "1", action: { type: "roll" } },
        { nid: "b", from: "2", action: { type: "roll" } },
      ],
      { ack: ["a"] },
    );
    expect(pending.map((p: { nid: string }) => p.nid)).toEqual(["b"]);
  });

  it("ignores an older host state write", () => {
    const cur = { hostId: "h", seq: 4, state: { turn: 1 }, pending: [] };
    const saved = patchRoom(cur, { asHost: true, hostId: "h", seq: 3, state: { turn: 0 } }, "r", "ABCD");
    expect(saved.state.turn).toBe(1);
    expect(saved.seq).toBe(4);
  });

  it("remaps a seat id onto a Discord id in play state", () => {
    const state = remapStateIds(
      {
        players: [{ id: "seat-1", name: "Guest", cash: 10 }],
        companies: { discord: { owner: "seat-1", level: 0 } },
        stores: { shop: "seat-1" },
      },
      "seat-1",
      "99",
      { id: "99", name: "Alex" },
    );
    expect(state.players[0].id).toBe("99");
    expect(state.players[0].name).toBe("Alex");
    expect(state.companies.discord.owner).toBe("99");
    expect(state.stores.shop).toBe("99");
  });

  it("delivers a handshake to every id the host might be using", () => {
    const me = uniqueIds("seat-aa", "99");
    expect(signalForMe({ from: "guest", to: "seat-aa" }, me, "99")).toBe(true);
    expect(signalForMe({ from: "guest", to: "99" }, me, "99")).toBe(true);
    expect(signalForMe({ from: "99", to: "guest" }, me, "99")).toBe(false);
  });

  it("stores a table-link pack on the wire", () => {
    const once = mergeWire([], { signal: { from: "g", to: "h", kind: "pack", payload: "{\"t\":\"hello\"}", nid: "p1" } });
    expect(once[0].payload).toContain("hello");
  });

  it("keeps handshake signals until they are acked", () => {
    const once = mergeWire([], { signal: { from: "a", to: "b", kind: "offer", sdp: "x", nid: "n1" } });
    expect(once).toHaveLength(1);
    const cleared = mergeWire(once, { wireAck: ["n1"] });
    expect(cleared).toHaveLength(0);
  });

  it("keeps a newer cached turn when folding a slow write", () => {
    const folded = foldRoom(
      { seq: 8, state: { turn: 2 }, pending: [{ nid: "x", action: { type: "roll" } }] },
      { seq: 7, state: { turn: 1 }, pending: [], ack: [] },
    );
    expect(folded.seq).toBe(8);
    expect(folded.state.turn).toBe(2);
    expect(folded.pending).toHaveLength(1);
  });
});
