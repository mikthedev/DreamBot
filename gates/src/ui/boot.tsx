import { useEffect, useState } from "react";
import { PixelDie } from "./marks";

export function usePocketDesk() {
  const [pocket, setPocket] = useState(() => cramped());
  useEffect(() => {
    const on = () => setPocket(cramped());
    on();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return pocket;
}

function cramped() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  return h < 520 || w < 460;
}

export function BootCurtain({ lines }: { lines: string[] }) {
  const [pip, setPip] = useState(5);
  useEffect(() => {
    const id = window.setInterval(() => setPip((n) => (n % 6) + 1), 140);
    return () => window.clearInterval(id);
  }, []);
  return (
    <div className="boot-curtain" role="status" aria-live="polite">
      <div className="boot-dice">
        <PixelDie n={pip} spinning />
        <PixelDie n={((pip + 2) % 6) + 1} spinning />
      </div>
      <p className="boot-title">DreamPoly</p>
      <p className="boot-line">{lines[0]}</p>
      <i className="boot-track" />
    </div>
  );
}

export function PocketDesk() {
  return (
    <div className="pocket-desk" role="dialog" aria-label="Expand DreamPoly to play">
      <div className="pocket-mark" aria-hidden>
        <span />
        <span />
        <span />
        <span />
      </div>
      <p className="pocket-kicker">Too small for a table</p>
      <h2>Open the full board</h2>
      <p>
        This peek can't hold DreamPoly. Hit Discord's expand arrows so everyone can sit, roll, and buy brands.
      </p>
    </div>
  );
}
