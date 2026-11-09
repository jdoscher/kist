// Print the contrast table for every theme (docs/brand.md quotes it):
//   node --experimental-strip-types scripts/contrast.ts
import { readFileSync } from 'node:fs'
import { checkThemes, parseThemes } from '../src/contrast.ts'

const css = readFileSync(new URL('../src/themes.css', import.meta.url), 'utf8')
const rows = checkThemes(parseThemes(css))
const themes = [...new Set(rows.map((r) => r.theme))]
console.log(`| Pair | Min | ${themes.join(' | ')} |`)
console.log(`|---|---|${themes.map(() => '---').join('|')}|`)
const keys = [...new Set(rows.map((r) => `${r.fg}|${r.bg}`))]
for (const k of keys) {
  const cells = themes.map((t) => {
    const r = rows.find((x) => x.theme === t && `${x.fg}|${x.bg}` === k)!
    return `${r.ratio.toFixed(2)}${r.ok ? '' : ' FAIL'}`
  })
  const r0 = rows.find((x) => `${x.fg}|${x.bg}` === k)!
  console.log(`| ${r0.what} (\`${r0.fg}\` on \`${r0.bg}\`) | ${r0.min} | ${cells.join(' | ')} |`)
}
const bad = rows.filter((r) => !r.ok)
console.log(bad.length ? `\n${bad.length} failing` : `\nall ${rows.length} pairs pass`)
process.exitCode = bad.length ? 1 : 0
