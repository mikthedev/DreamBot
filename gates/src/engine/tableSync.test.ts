import { describe, expect, it } from "vitest";
import { patchRoom } from "../../api/roomPeople.js";
import { createGame } from "./game";
import { guestShouldApply, isPlaceholderName, publicSeatName, sanitizeHostSnap } from "./tableSync";

describe("table sync over the room API (Discord has no WebRTC)", () => {
  it("never treats You/Guest as a real seat name", () => {
    expect(isPlaceholderName("You")).toBe(true);
    expect(isPlaceholderName("Guest")).toBe(true);
    expect(publicSeatName("You")).toBe("Player");
    expect(publicSeatName("Alex")).toBe("Alex");
  });

  it("lets a guest enter play from a room poll after the host starts", () => {
    const host = { id: "111", name: "HostFriend", avatar: null };
    const guest = { id: "222", name: "Joiner", avatar: null };
    const state = createGame(
      [
        { id: host.id, name: host.name, avatar: null },
        { id: guest.id, name: guest.name, avatar: null },
        { id: "bot-1", name: "Bot", avatar: null, bot: true },
      ],
      { seed: 9 },
    );

    let room = patchRoom(
      {},
      {
        asHost: true,
        claimHost: host.id,
        hostId: host.id,
        hostName: host.name,
        screen: "lobby",
        mode: "moves",
        joined: [host],
        spectators: [],
        state: null,
        seq: 1,
      },
      "inst-1",
      "ABCD",
    );
    room = patchRoom(room, { sit: guest }, "inst-1", "ABCD");

    const guestApplied = Number(room.seq || 0);
    const guestScreen = String(room.screen || "lobby");
    expect(guestScreen).toBe("lobby");
    expect(room.joined).toHaveLength(2);

    room = patchRoom(
      room,
      {
        asHost: true,
        claimHost: host.id,
        hostId: host.id,
        wasHost: host.id,
        hostName: host.name,
        screen: "play",
        mode: "moves",
        joined: [host, guest],
        spectators: [],
        state,
        seq: guestApplied + 1,
      },
      "inst-1",
      "ABCD",
    );

    expect(guestShouldApply(room, guestApplied, guestScreen, false)).toBe(true);
    expect(room.screen).toBe("play");
    expect(room.state).toBeTruthy();
    expect(room.joined.find((p: { id: string }) => p.id === host.id).name).toBe("HostFriend");
  });

  it("applies a play snap even when seq did not bump if the guest is still in lobby", () => {
    expect(guestShouldApply({ seq: 2, screen: "play", state: { turn: 0 } }, 2, "lobby", false)).toBe(true);
    expect(guestShouldApply({ seq: 2, screen: "lobby" }, 2, "lobby", false)).toBe(false);
  });

  it("strips You from a host snap before it is shared", () => {
    const clean = sanitizeHostSnap({
      hostId: "111",
      hostName: "You",
      screen: "lobby",
      mode: "moves",
      joined: [{ id: "111", name: "You", avatar: null }],
      spectators: [],
      state: null,
      seq: 2,
    });
    expect(clean.hostName).toBe("Host");
    expect(clean.joined[0].name).toBe("Player");
    expect(isPlaceholderName(clean.hostName)).toBe(false);
  });
});
