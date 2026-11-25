import { mkdirSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = join(root, "public/brands");
mkdirSync(outDir, { recursive: true });

const JOBS = [
  { id: "pepsi", files: ["Pepsi 2023.svg"] },
  { id: "pepsico", files: ["PepsiCo logo.svg"] },
  { id: "dunkin", files: ["Dunkin' logo.svg"] },
  { id: "timhortons", files: ["Tim Hortons logo.svg"] },
  { id: "wendys", files: ["Wendy's logo 2012.svg", "Wendy's logo.svg"] },
  { id: "dominos", files: ["Domino's pizza logo.svg"] },
  { id: "pizzahut", files: ["Pizza Hut (2019).svg"] },
  { id: "papajohns", files: ["Papa John's Logo 2019.svg", "Papa John's 2019 logo.svg"] },
  { id: "sysco", files: ["Sysco logo.svg"] },
  { id: "usfoods", files: ["US Foods logo.svg"] },
  { id: "gordonfood", files: ["Gordon Food Service logo.svg", "Gordon Food Service.svg"] },
  { id: "cargill", files: ["CargillLogo.svg", "Cargill logo.svg"] },
  { id: "tyson", files: ["Tyson Foods corporate logo (2024).svg", "Tyson Foods logo.svg"] },
  { id: "nestle", files: ["Nestlé textlogo.svg", "Nestle textlogo.svg"] },
  { id: "seveneleven", files: ["7-eleven logo.svg"] },
  { id: "costco", files: ["Costco Wholesale logo 2010-10-26.svg"] },
  { id: "wholefoods", files: ["Whole Foods Market logo.svg"] },
  { id: "levis", files: ["Levi's logo.svg"] },
  { id: "gap", files: ["Gap Inc. logo 2024.svg", "Gap logo.svg"] },
  { id: "carhartt", files: ["Carhartt logo.svg", "Carhartt wordmark.svg"] },
  { id: "sephora", files: ["Sephora logo.svg"] },
  { id: "ulta", files: ["Ulta Beauty logo.svg"] },
  { id: "mac", files: ["MAC Cosmetics logo.svg", "MAC logo.svg"] },
  { id: "dupont", files: ["DuPont logo.svg", "DuPont.svg"] },
  { id: "goretex", files: ["Gore-Tex logo.svg", "GORE-TEX logo.svg"] },
  { id: "lvmh", files: ["LVMH logo.svg", "LVMH.svg"] },
  { id: "kering", files: ["Kering-logo.svg", "Kering logo.svg"] },
  { id: "richemont", files: ["Richemont logo.svg"] },
  { id: "nordstrom", files: ["Nordstrom logo.svg"] },
  { id: "tkmaxx", files: ["TK Maxx logo.svg", "T.K. Maxx logo.svg"] },
  { id: "footlocker", files: ["Foot Locker 2020 Wordmark Logo.svg", "Foot Locker logo.svg"] },
  { id: "fanatics", files: ["Fanatics logo.svg", "Fanatics Inc logo.svg"] },
  { id: "lids", files: ["Lids logo.svg"] },
  { id: "mitchellness", files: ["Mitchell & Ness logo.svg"] },
  { id: "wilson", files: ["Wilson Sporting Goods logo.svg", "Wilson logo.svg"] },
  { id: "spalding", files: ["Spalding logo.svg"] },
  { id: "rawlings", files: ["Rawlings logo.svg"] },
  { id: "espn", files: ["ESPN wordmark.svg"] },
  { id: "msg", files: ["Madison Square Garden logo.svg", "MSG logo.svg"] },
  { id: "wembley", files: ["Wembley Stadium logo.svg"] },
  { id: "sofi", files: ["SoFi logo.svg", "Social Finance logo.svg"] },
  { id: "waymo", files: ["Waymo logo.svg"] },
];

const UA = "DreamPolyAssetFit/1.0 (local board game; Wikimedia text logos)";

function lightFill(v) {
  const s = String(v).trim().toLowerCase();
  if (s === "none" || s === "transparent") return "none";
  if (s === "white" || s === "#fff" || s === "#ffffff" || s === "#fffefe") return "drop";
  if (s.startsWith("rgb(") && /rgb\(\s*25[0-5]\s*,\s*25[0-5]\s*,\s*25[0-5]/.test(s)) return "drop";
  return "ink";
}

function inkAttrs(src) {
  return src
    .replace(/\sfill="([^"]*)"/gi, (_, v) => {
      const k = lightFill(v);
      if (k === "none") return ` fill="none"`;
      if (k === "drop") return ` fill="none"`;
      return ` fill="currentColor"`;
    })
    .replace(/\sstroke="([^"]*)"/gi, (_, v) => {
      const k = lightFill(v);
      if (k === "none") return ` stroke="none"`;
      if (k === "drop") return ` stroke="none"`;
      return ` stroke="currentColor"`;
    })
    .replace(/\sstyle="([^"]*)"/gi, (_, style) => {
      const next = style
        .replace(/fill\s*:\s*[^;]+/gi, (m) => {
          const v = m.split(":")[1];
          const k = lightFill(v);
          if (k === "none" || k === "drop") return "fill:none";
          return "fill:currentColor";
        })
        .replace(/stroke\s*:\s*[^;]+/gi, (m) => {
          const v = m.split(":")[1];
          const k = lightFill(v);
          if (k === "none" || k === "drop") return "stroke:none";
          return "stroke:currentColor";
        });
      return ` style="${next}"`;
    });
}

function viewBoxOf(svg) {
  const vb = svg.match(/viewBox=["']([^"']+)["']/i);
  if (vb) {
    const p = vb[1].trim().split(/[\s,]+/).map(Number);
    if (p.length === 4 && p.every((n) => Number.isFinite(n))) return p;
  }
  const w = Number((svg.match(/\bwidth=["']([\d.]+)/i) || [])[1]);
  const h = Number((svg.match(/\bheight=["']([\d.]+)/i) || [])[1]);
  if (w && h) return [0, 0, w, h];
  return [0, 0, 24, 24];
}

function inner(svg) {
  const open = svg.search(/<svg[\s>]/i);
  const close = svg.lastIndexOf("</svg>");
  if (open < 0 || close < 0) return svg;
  const tagEnd = svg.indexOf(">", open);
  return svg.slice(tagEnd + 1, close);
}

function fit(svg) {
  let body = svg.replace(/<\?xml[^>]*>/g, "").replace(/<!DOCTYPE[\s\S]*?>/g, "");
  body = body.replace(/<!--([\s\S]*?)-->/g, "");
  body = body.replace(/<script[\s\S]*?<\/script>/gi, "");
  body = inkAttrs(body);
  const [x, y, w, h] = viewBoxOf(body);
  const pad = 1.2;
  const scale = Math.min((24 - pad * 2) / w, (24 - pad * 2) / h);
  const tx = (24 - w * scale) / 2 - x * scale;
  const ty = (24 - h * scale) / 2 - y * scale;
  const guts = inner(body)
    .replace(/<\?xml[\s\S]*?\?>/g, "")
    .trim();
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><g transform="translate(${tx.toFixed(3)} ${ty.toFixed(3)}) scale(${scale.toFixed(5)})">${guts}</g></svg>`;
}

async function grab(file) {
  const url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + encodeURIComponent(file);
  const res = await fetch(url, { headers: { "User-Agent": UA }, redirect: "follow" });
  if (!res.ok) throw new Error(`${res.status} ${file}`);
  const text = await res.text();
  if (!text.includes("<svg") && !text.includes("<SVG")) throw new Error(`not svg ${file}`);
  return text;
}

const failed = [];
for (const job of JOBS) {
  let ok = false;
  for (const file of job.files) {
    try {
      const raw = await grab(file);
      const fitted = fit(raw);
      writeFileSync(join(outDir, `${job.id}.svg`), fitted);
      console.log("ok", job.id, file, fitted.length);
      ok = true;
      break;
    } catch (err) {
      console.log("miss", job.id, file, err.message);
    }
  }
  if (!ok) failed.push(job.id);
}

for (const id of ["disney", "azure", "microsoft", "xai"]) {
  const p = join(outDir, `${id}.svg`);
  if (!existsSync(p)) continue;
  const fitted = fit(readFileSync(p, "utf8"));
  writeFileSync(p, fitted);
  console.log("refit", id, fitted.length);
}

console.log("FAILED", failed.join(",") || "none");
