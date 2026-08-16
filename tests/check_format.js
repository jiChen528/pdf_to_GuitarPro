// 验证 alphaTab 能否读取某个吉他谱文件（同步 API）
const fs = require("fs");
const alphaTab = require("@coderline/alphatab");

const file = process.argv[2];
if (!file) {
  console.error("用法: node check_format.js 文件.gp");
  process.exit(1);
}
const bytes = new Uint8Array(fs.readFileSync(file));
try {
  const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(bytes, new alphaTab.Settings());
  console.log("LOAD_OK");
  console.log("title:", score.title, "| tracks:", score.tracks.length,
    "| masterBars:", score.masterBars.length);
  const notes = [];
  for (const bar of score.tracks[0].staves[0].bars) {
    for (const voice of bar.voices) {
      for (const beat of voice.beats) {
        for (const note of beat.notes) {
          notes.push([note.string, note.fret]);
        }
      }
    }
  }
  console.log("notes:", notes.length, JSON.stringify(notes.slice(0, 20)));
} catch (e) {
  console.log("LOAD_FAIL:", e && e.message ? e.message : String(e));
  process.exit(1);
}
