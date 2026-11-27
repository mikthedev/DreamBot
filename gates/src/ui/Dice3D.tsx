import { useEffect, useRef, useState } from "react";
import { sfxDiceBounce } from "./sfx";

type Props = {
  roll: [number, number];
  rollKey: number;
  doublesLabel?: string;
  onSettled: (rollKey: number) => void;
};

type DieBody = {
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  rx: number;
  ry: number;
  rz: number;
  wx: number;
  wy: number;
  wz: number;
  targetRx: number;
  targetRy: number;
  targetRz: number;
  face: number;
  bounces: number;
};

const TARGET_ROT: Record<number, [number, number]> = {
  1: [0, 0],
  2: [0, -90],
  3: [-90, 0],
  4: [90, 0],
  5: [0, 90],
  6: [0, 180],
};

// Local outward normal vectors of faces 1..6 for live 3D shading during tumble
const FACE_NORMALS: [number, number, number][] = [
  [0, 0, 1],   // f1
  [0, 0, -1],  // f6
  [1, 0, 0],   // f2
  [-1, 0, 0],  // f5
  [0, -1, 0],  // f3
  [0, 1, 0],   // f4
];

const PIP_COORDS: Record<number, [number, number][]> = {
  1: [[50, 50]],
  2: [[28, 28], [72, 72]],
  3: [[28, 28], [50, 50], [72, 72]],
  4: [[28, 28], [72, 28], [28, 72], [72, 72]],
  5: [[28, 28], [72, 28], [50, 50], [28, 72], [72, 72]],
  6: [[28, 25], [72, 25], [28, 50], [72, 50], [28, 75], [72, 75]],
};

function seededRng(seed: number) {
  let s = (seed * 9301 + 49297) % 233280;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function rotateNormal(nx: number, ny: number, nz: number, rxDeg: number, ryDeg: number, rzDeg: number): [number, number, number] {
  const rx = (rxDeg * Math.PI) / 180;
  const ry = (ryDeg * Math.PI) / 180;
  const rz = (rzDeg * Math.PI) / 180;
  // R_z * R_x * R_y matching CSS transform order
  const cy = Math.cos(ry), sy = Math.sin(ry);
  const x1 = nx * cy + nz * sy;
  const z1 = -nx * sy + nz * cy;
  const cx = Math.cos(rx), sx = Math.sin(rx);
  const y2 = ny * cx - z1 * sx;
  const z2 = ny * sx + z1 * cx;
  const cz = Math.cos(rz), sz = Math.sin(rz);
  const x3 = x1 * cz - y2 * sz;
  const y3 = x1 * sz + y2 * cz;
  return [x3, y3, z2];
}

function DieFace({
  n,
  cls,
  faceRef,
}: {
  n: number;
  cls: string;
  faceRef: (el: HTMLSpanElement | null) => void;
}) {
  return (
    <span ref={faceRef} className={`d3-face ${cls}${n === 1 ? " is-ace" : ""}`} aria-hidden>
      {PIP_COORDS[n].map(([px, py], i) => (
        <i key={i} className="d3-pip" style={{ left: `${px}%`, top: `${py}%` }} />
      ))}
    </span>
  );
}

/**
 * Heavy 3D rigid-body dice tray simulation:
 * launches two chunky beveled cubes upward over the board arena, collides across
 * 4-5 elastic floor & die-to-die impacts with dynamic normal lighting and cast shadows,
 * then holds cleanly on the landed faces before pawns begin moving.
 */
export function BoardDice3D({ roll, rollKey, doublesLabel, onSettled }: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const cubeRefs = useRef<[HTMLDivElement | null, HTMLDivElement | null]>([null, null]);
  const shadowRefs = useRef<[HTMLDivElement | null, HTMLDivElement | null]>([null, null]);
  const faceRefs = useRef<(HTMLSpanElement | null)[][]>([
    [null, null, null, null, null, null],
    [null, null, null, null, null, null],
  ]);
  const [phase, setPhase] = useState<"falling" | "settled" | "fading">("falling");
  const settledCb = useRef(onSettled);
  settledCb.current = onSettled;

  useEffect(() => {
    setPhase("falling");
    const rand = seededRng(rollKey * 131 + roll[0] * 17 + roll[1] * 31);
    const makeBody = (idx: number, face: number): DieBody => {
      const [baseRx, baseRy] = TARGET_ROT[face] ?? [0, 0];
      const spinX = (idx === 0 ? 1 : -1) * (3 + Math.floor(rand() * 2)) * 360;
      const spinY = (idx === 0 ? -1 : 1) * (3 + Math.floor(rand() * 2)) * 360;
      const sideSign = idx === 0 ? -1 : 1;
      return {
        x: sideSign * (86 + rand() * 34),
        y: -52 + rand() * 44,
        z: 225 + idx * 28 + rand() * 36,
        vx: -sideSign * (96 + rand() * 68) + (rand() - 0.5) * 44,
        vy: 62 + (rand() - 0.5) * 96,
        vz: 65 + rand() * 65,
        rx: baseRx - spinX + (rand() - 0.5) * 110,
        ry: baseRy - spinY + (rand() - 0.5) * 110,
        rz: (rand() - 0.5) * 180,
        wx: spinX * 0.92,
        wy: spinY * 0.92,
        wz: (rand() - 0.5) * 460,
        targetRx: baseRx,
        targetRy: baseRy,
        targetRz: sideSign * (10 + Math.round(rand() * 14)),
        face,
        bounces: 0,
      };
    };

    const bodies: [DieBody, DieBody] = [makeBody(0, roll[0]), makeBody(1, roll[1])];
    const G = 1520;
    const RESTITUTION = 0.55;
    const DURATION_SIM = 1.52;
    const HOLD_READ = 0.68;
    const start = performance.now();
    let prev = start;
    let raf = 0;
    let done = false;
    let fadeTimer = 0;

    const applyDom = (b: DieBody, idx: 0 | 1, blend: number) => {
      const cube = cubeRefs.current[idx];
      const shadow = shadowRefs.current[idx];
      if (!cube || !shadow) return;
      const curRx = b.rx * (1 - blend) + b.targetRx * blend;
      const curRy = b.ry * (1 - blend) + b.targetRy * blend;
      const curRz = b.rz * (1 - blend) + b.targetRz * blend;
      const scaleAir = 1 + Math.min(0.48, b.z / 380);
      cube.style.transform = `translate3d(${b.x.toFixed(1)}px, ${(b.y - b.z * 0.44).toFixed(1)}px, ${b.z.toFixed(1)}px) scale(${scaleAir.toFixed(3)}) rotateZ(${curRz.toFixed(1)}deg) rotateX(${curRx.toFixed(1)}deg) rotateY(${curRy.toFixed(1)}deg)`;
      const shScale = Math.max(0.42, 1.06 - b.z / 380);
      const shAlpha = Math.max(0.18, 0.64 - b.z / 440);
      shadow.style.transform = `translate3d(${(b.x + b.z * 0.08).toFixed(1)}px, ${(b.y + 12).toFixed(1)}px, 0) scale(${(shScale * 1.08).toFixed(3)}, ${(shScale * 0.66).toFixed(3)})`;
      shadow.style.opacity = shAlpha.toFixed(2);

      // Update per-face directional lighting as cube tumbles in 3D
      const faces = faceRefs.current[idx];
      for (let f = 0; f < 6; f++) {
        const el = faces[f];
        if (!el) continue;
        const [nx, ny, nz] = FACE_NORMALS[f];
        const [wx, wy, wz] = rotateNormal(nx, ny, nz, curRx, curRy, curRz);
        const light = Math.max(0, Math.min(1, 0.52 + (wz * 0.72 - wx * 0.25 - wy * 0.3) * 0.42));
        const br = 0.76 + light * 0.3;
        el.style.filter = `brightness(${br.toFixed(3)})`;
      }
    };

    const tick = (now: number) => {
      const dt = Math.min(0.032, Math.max(0.004, (now - prev) / 1000));
      const elapsed = (now - start) / 1000;
      prev = now;

      const stage = stageRef.current;
      const cubeW = cubeRefs.current[0]?.offsetWidth || 60;
      const halfW = Math.max(106, (stage?.clientWidth ?? 340) * 0.40);
      const halfH = Math.max(88, (stage?.clientHeight ?? 340) * 0.36);
      const minDist = Math.max(96, Math.round(cubeW * 1.60));

      for (let i = 0; i < 2; i++) {
        const b = bodies[i];
        b.vz -= G * dt;
        b.x += b.vx * dt;
        b.y += b.vy * dt;
        b.z += b.vz * dt;
        b.vx *= 1 - 1.25 * dt;
        b.vy *= 1 - 1.25 * dt;
        b.rx += b.wx * dt;
        b.ry += b.wy * dt;
        b.rz += b.wz * dt;
        b.wx *= 1 - 1.75 * dt;
        b.wy *= 1 - 1.75 * dt;
        b.wz *= 1 - 2.1 * dt;

        // Tray wall rebounds
        if (b.x < -halfW) {
          if (Math.abs(b.vx) > 90 && b.bounces <= 5) sfxDiceBounce(Math.min(0.65, Math.abs(b.vx) / 420), i);
          b.x = -halfW;
          b.vx = Math.abs(b.vx) * 0.72;
          b.wz += (rand() - 0.5) * 140;
        }
        if (b.x > halfW) {
          if (Math.abs(b.vx) > 90 && b.bounces <= 5) sfxDiceBounce(Math.min(0.65, Math.abs(b.vx) / 420), i);
          b.x = halfW;
          b.vx = -Math.abs(b.vx) * 0.72;
          b.wz += (rand() - 0.5) * 140;
        }
        if (b.y < -halfH) {
          if (Math.abs(b.vy) > 90 && b.bounces <= 5) sfxDiceBounce(Math.min(0.65, Math.abs(b.vy) / 420), i);
          b.y = -halfH;
          b.vy = Math.abs(b.vy) * 0.72;
        }
        if (b.y > halfH) {
          if (Math.abs(b.vy) > 90 && b.bounces <= 5) sfxDiceBounce(Math.min(0.65, Math.abs(b.vy) / 420), i);
          b.y = halfH;
          b.vy = -Math.abs(b.vy) * 0.72;
        }

        // Elastic floor impact + angular deflect
        if (b.z <= 0) {
          const speed = Math.abs(b.vz);
          b.z = 0;
          if (speed > 70) {
            b.vz = speed * RESTITUTION;
            b.vx *= 0.78;
            b.vy *= 0.78;
            b.wx = b.wx * 0.66 + (rand() - 0.5) * speed * 0.65;
            b.wy = b.wy * 0.66 + (rand() - 0.5) * speed * 0.65;
            b.bounces += 1;
            if (b.bounces <= 4) sfxDiceBounce(Math.min(1, speed / 520), i);
          } else {
            b.vz = 0;
            b.vx *= 0.45;
            b.vy *= 0.45;
          }
        }
      }

      // Elastic momentum exchange on die-to-die impact
      const dx = bodies[1].x - bodies[0].x;
      const dy = bodies[1].y - bodies[0].y;
      const dist = Math.hypot(dx, dy);
      if (dist < minDist && Math.abs(bodies[1].z - bodies[0].z) < 58) {
        const nx = dist > 0.1 ? dx / dist : 1;
        const ny = dist > 0.1 ? dy / dist : 0;
        const overlap = (minDist - dist) * 0.58;
        bodies[0].x -= nx * overlap;
        bodies[0].y -= ny * overlap;
        bodies[1].x += nx * overlap;
        bodies[1].y += ny * overlap;
        const rvx = bodies[1].vx - bodies[0].vx;
        const rvy = bodies[1].vy - bodies[0].vy;
        const velAlongNormal = rvx * nx + rvy * ny;
        if (velAlongNormal < 0) {
          const impulse = -(1 + 0.75) * velAlongNormal * 0.5;
          bodies[0].vx -= impulse * nx;
          bodies[0].vy -= impulse * ny;
          bodies[1].vx += impulse * nx;
          bodies[1].vy += impulse * ny;
          bodies[0].wz -= 160;
          bodies[1].wz += 160;
          sfxDiceBounce(0.72, 1);
        }
      }

      // Smoothly converge orientation onto exact target face in final ~42% of simulation
      const align = Math.max(0, Math.min(1, (elapsed - DURATION_SIM * 0.55) / (DURATION_SIM * 0.45)));
      const smooth = align * align * (3 - 2 * align);
      applyDom(bodies[0], 0, smooth);
      applyDom(bodies[1], 1, smooth);

      if (elapsed < DURATION_SIM) {
        raf = window.requestAnimationFrame(tick);
        return;
      }

      bodies[0].z = 0;
      bodies[1].z = 0;
      applyDom(bodies[0], 0, 1);
      applyDom(bodies[1], 1, 1);

      if (!done) {
        done = true;
        setPhase("settled");
        fadeTimer = window.setTimeout(() => {
          settledCb.current(rollKey);
          setPhase("fading");
        }, Math.round(HOLD_READ * 1000));
      }
    };

    raf = window.requestAnimationFrame(tick);
    return () => {
      window.cancelAnimationFrame(raf);
      window.clearTimeout(fadeTimer);
    };
  }, [rollKey, roll[0], roll[1]]);

  const sum = roll[0] + roll[1];
  const isDoubles = roll[0] === roll[1];

  return (
    <div ref={stageRef} className={`dice3d-arena is-${phase}`} aria-hidden>
      <div ref={(el) => { shadowRefs.current[0] = el; }} className="d3-shadow" />
      <div ref={(el) => { shadowRefs.current[1] = el; }} className="d3-shadow" />
      {[0, 1].map((idx) => (
        <div
          key={idx}
          ref={(el) => { cubeRefs.current[idx] = el; }}
          className={`d3-cube${isDoubles && phase !== "falling" ? " is-doubles" : ""}`}
        >
          <DieFace n={1} cls="f1" faceRef={(el) => { faceRefs.current[idx][0] = el; }} />
          <DieFace n={6} cls="f6" faceRef={(el) => { faceRefs.current[idx][1] = el; }} />
          <DieFace n={2} cls="f2" faceRef={(el) => { faceRefs.current[idx][2] = el; }} />
          <DieFace n={5} cls="f5" faceRef={(el) => { faceRefs.current[idx][3] = el; }} />
          <DieFace n={3} cls="f3" faceRef={(el) => { faceRefs.current[idx][4] = el; }} />
          <DieFace n={4} cls="f4" faceRef={(el) => { faceRefs.current[idx][5] = el; }} />
        </div>
      ))}
      {phase !== "falling" ? (
        <div className={`d3-readout${isDoubles ? " is-doubles" : ""}`}>
          <b>{sum}</b>
          {isDoubles ? <em>{doublesLabel || "DOUBLES!"}</em> : null}
        </div>
      ) : null}
    </div>
  );
}
