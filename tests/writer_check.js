// 写文件器自检: 写入 .gp 后用 alphaTab 回读，校验音符数量/品位/调弦方向
const fs = require("fs");
const alphaTab = require("@coderline/alphatab");

const spec = {
  title: "Writer Test",
  artist: "Tester",
  tempo: 120,
  tuning: [40, 45, 50, 55, 59, 64], // 低音弦 -> 高音弦
  tracks: [
    {
      name: "Guitar",
      tuning: [40, 45, 50, 55, 59, 64],
      measures: [
        {
          timeSignature: { numerator: 4, denominator: 4 },
          beats: [
            { duration: 4, notes: [{ string: 1, fret: 0 }, { string: 6, fret: 0 }] },
            { duration: 8, notes: [{ string: 6, fret: 3 }] },
            { duration: 8, notes: [{ string: 1, fret: 12 }] },
            { duration: 4, rest: true },
          ],
        },
        {
          timeSignature: { numerator: 4, denominator: 4 },
          beats: [{ duration: 4, notes: [{ string: 5, fret: 7 }] }],
        },
      ],
    },
  ],
};

const outPath = "tests/out/writer_test.gp";
fs.writeFileSync("tests/out/writer_spec.json", JSON.stringify(spec));

// 直接调用 writer 模块逻辑（内联构建）
const alphaTabModel = alphaTab.model;
function buildScore(spec) {
  const score = new alphaTabModel.Score();
  score.title = spec.title || "";
  score.artist = spec.artist || "";
  score.tempo = spec.tempo || 120;
  for (const trackData of spec.tracks || []) {
    const track = new alphaTabModel.Track();
    track.ensureStaveCount(1);
    track.name = trackData.name || "Guitar";
    const staff = track.staves[0];
    staff.showTablature = true;
    staff.showStandardNotation = false;
    if (Array.isArray(trackData.tuning) && trackData.tuning.length > 0) {
      staff.stringTuning.tunings = trackData.tuning.slice();
      staff.stringTuning.isPreset = false;
      staff.stringTuning.name = "custom";
    }
    score.addTrack(track);
    for (const mData of trackData.measures || []) {
      const bar = new alphaTabModel.Bar();
      const voice = new alphaTabModel.Voice();
      for (const bData of mData.beats || []) {
        const beat = new alphaTabModel.Beat();
        beat.duration = bData.duration || 8;
        if (bData.dotted) beat.dots = 1;
        if (!bData.rest) {
          for (const nData of bData.notes || []) {
            const note = new alphaTabModel.Note();
            note.string = nData.string;
            note.fret = nData.fret;
            beat.addNote(note);
          }
        }
        voice.addBeat(beat);
      }
      bar.addVoice(voice);
      staff.addBar(bar);
    }
  }
  return score;
}

const score = buildScore(spec);
const settings = new alphaTab.Settings();

// 每个小节对应一个 MasterBar（索引对齐），否则 finish/导出会崩溃
console.log("Automation:", typeof alphaTabModel.Automation, "AutomationType:", typeof alphaTabModel.AutomationType);
const totalBars = score.tracks[0].staves[0].bars.length;
for (let i = 0; i < totalBars; i++) {
  const mb = new alphaTabModel.MasterBar();
  mb.timeSignatureNumerator = 4;
  mb.timeSignatureDenominator = 4;
  score.addMasterBar(mb);
}
const tempoAutomation = new alphaTabModel.Automation();
tempoAutomation.isLinear = false;
tempoAutomation.type = alphaTabModel.AutomationType.Tempo;
tempoAutomation.value = score.tempo;
score.masterBars[0].tempoAutomations.push(tempoAutomation);

score.finish(settings);
const exporter = new alphaTab.exporter.Gp7Exporter();
const data = exporter.export(score, settings);
fs.writeFileSync(outPath, Buffer.from(data));
console.log("WROTE", outPath, data.length, "bytes");

// 回读校验
const bytes = new Uint8Array(fs.readFileSync(outPath));
try {
  const loaded = alphaTab.importer.ScoreLoader.loadScoreFromBytes(bytes, new alphaTab.Settings());
    console.log("LOAD_OK title=", loaded.title, "tempo=", loaded.tempo,
      "tracks=", loaded.tracks.length, "masterBars=", loaded.masterBars.length);
    const track = loaded.tracks[0];
    console.log("tuning(低->高):", JSON.stringify(track.staves[0].stringTuning.tunings));
    const notes = [];
    for (const bar of track.staves[0].bars) {
      for (const voice of bar.voices) {
        for (const beat of voice.beats) {
          for (const note of beat.notes) {
            notes.push({
              string: note.string,
              fret: note.fret,
              midi: note.realValue,
              dur: beat.duration,
            });
          }
        }
      }
    }
    console.log("notes:", JSON.stringify(notes));
    const expect = [
      { string: 1, fret: 0, midi: 64 },
      { string: 6, fret: 0, midi: 40 },
      { string: 6, fret: 3, midi: 43 },
      { string: 1, fret: 12, midi: 76 },
      { string: 5, fret: 7, midi: 52 },
    ];
    let ok = notes.length === expect.length;
    for (let i = 0; i < expect.length && ok; i++) {
      const e = expect[i], a = notes[i];
      ok = a.string === e.string && a.fret === e.fret && a.midi === e.midi;
      if (!ok) console.log("MISMATCH at", i, "got", JSON.stringify(a), "want", JSON.stringify(e));
    }
  console.log(ok ? "PASS: 写/读回一致" : "FAIL");
  process.exit(ok ? 0 : 1);
} catch (e) {
  console.log("LOAD_FAIL:", e && e.message ? e.message : String(e));
  process.exit(1);
}
