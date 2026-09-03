import { chromium } from "playwright";
const OUT = process.env.SHOTS, BASE = "http://localhost:3000";
const b = await chromium.launch();
const problems = [];
async function shot(path, file, { dark = false, w = 1440, h = 950, click, wait = 1800 } = {}) {
  const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
  const pg = await ctx.newPage();
  pg.on("pageerror", (e) => problems.push(`${file}: PAGEERROR ${String(e).slice(0, 130)}`));
  if (dark) await ctx.addInitScript(() => localStorage.setItem("zonuly-theme", "dark"));
  await pg.goto(BASE + path, { waitUntil: "domcontentloaded", timeout: 90000 });
  await pg.waitForTimeout(wait);
  if (click) for (const c of [].concat(click)) {
    try { await pg.click(c, { timeout: 6000 }); await pg.waitForTimeout(800); }
    catch { problems.push(`${file}: could not click ${c}`); }
  }
  const ov = await pg.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (ov > 2) problems.push(`${file}: horizontal overflow ${ov}px`);
  await pg.screenshot({ path: `${OUT}/${file}.png` });
  await ctx.close();
}
await shot("/companies", "10-companies");
await shot("/companies", "11-companies-open", { click: ["li[data-row] button >> nth=0"] });
await shot("/companies", "12-companies-dark", { dark: true, click: ["li[data-row] button >> nth=0"] });
await shot("/companies", "13-companies-mobile", { w: 390, h: 844 });
await b.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.map((p) => "  " + p).join("\n") : "clean");
