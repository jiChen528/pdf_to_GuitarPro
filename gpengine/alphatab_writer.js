// alphatab_writer.js
// 把转换 spec(JSON) 写成真正的 Guitar Pro 7 (.gp) 文件（Guitar Pro 8 可直接打开）
//
// 用法: node alphatab_writer.js spec.json 输出.gp
// spec 结构:
//   { title, artist, tempo,
//     tracks: [{ name, tuning: [低音弦..高音弦 MIDI], measures: [
//       { timeSignature: {numerator, denominator},
//         beats: [{ duration, dotted, rest, notes: [{string: 1起, fret}] }] }] }] }
//
// 说明: quinnjr/guitar-pro-mcp 输出的 .gp6 是自定义二进制格式，GP8/alphaTab 都无法解析，
//       因此改用 alphaTab 的 Gp7Exporter 输出标准 .gp 文件。
const fs = require("fs");
// 打包运行时不依赖 node_modules：优先用环境变量指定的 alphaTab.js 绝对路径
const alphaTab = require(process.env.ALPHATAB_JS || "@coderline/alphatab");
const M = alphaTab.model;

function buildScore(spec) {
    const score = new M.Score();
    score.title = spec.title || "";
    score.artist = spec.artist || "";
    score.tempo = spec.tempo || 120;

    for (const trackData of spec.tracks || []) {
        const track = new M.Track();
        track.ensureStaveCount(1);
        track.name = trackData.name || "Guitar";
        track.playbackInfo.program = 29; // 失真/过载电吉他（General MIDI: Overdriven Guitar）
        const staff = track.staves[0];
        staff.showTablature = true;
        staff.showStandardNotation = false;
        if (Array.isArray(trackData.tuning) && trackData.tuning.length > 0) {
            staff.stringTuning.tunings = trackData.tuning.slice();
            staff.stringTuning.isPreset = false;
            staff.stringTuning.name = "custom";
        }
        score.addTrack(track);

        const measures = trackData.measures || [];
        let prevNotes = null; // 上一小节最后一个拍的音符（空小节延音用）
        const lastByString = new Map(); // 弦 -> 上一个音符（延音线链接）
        for (const mData of measures) {
            const bar = new M.Bar();
            bar.keySignature = 0;
            bar.keySignatureType = M.KeySignatureType.Major;
            const voice = new M.Voice();
            const beats = mData.beats || [];
            if (beats.length === 0) {
                // 空小节（延长音/留白）: 延音上一小节最后一个拍；没有则补全音符休止
                const beat = new M.Beat();
                beat.duration = 1;
                if (prevNotes && prevNotes.length > 0) {
                    for (const pn of prevNotes) {
                        const note = new M.Note();
                        note.string = pn.string;
                        note.fret = pn.fret;
                        note.isDead = pn.isDead;
                        note.isPalmMute = pn.isPalmMute;
                        note.tieOrigin = pn;
                        pn.tieDestination = note;
                        note.isTieDestination = true;
                        beat.addNote(note);
                        lastByString.set(note.string, note);
                    }
                }
                voice.addBeat(beat);
                prevNotes = beat.notes.length > 0 ? beat.notes : null;
            } else {
                for (const bData of beats) {
                    const beat = new M.Beat();
                    beat.duration = bData.duration || 8;
                    if (bData.dotted) {
                        beat.dots = 1;
                    }
                    if (bData.rest) {
                        lastByString.clear();  // 休止打断延音链
                    } else {
                        for (const nData of bData.notes || []) {
                            const note = new M.Note();
                            note.string = nData.string;
                            note.fret = nData.fret;
                            if (nData.dead) {
                                note.isDead = true;          // 哑音/左手闷音(tab 中显示 x)
                            }
                            if (nData.palmMute) {
                                note.isPalmMute = true;      // 右手掌闷音(P.M.)
                            }
                            if (nData.tied && lastByString.has(nData.string)) {
                                note.tieOrigin = lastByString.get(nData.string);
                                note.tieOrigin.tieDestination = note;
                                note.isTieDestination = true;
                            }
                            if (nData.hopoOrigin) {
                                note.isHammerPullOrigin = true;      // 击勾弦起点
                            }
                            if (nData.hopoDestination) {
                                note.isHammerPullDestination = true; // 击勾弦终点
                            }
                            if (nData.slideOut) {
                                note.slideOutType = M.SlideOutType.Shift;  // 普通滑弦起点
                            }
                            if (nData.legatoSlideOut) {
                                note.slideOutType = M.SlideOutType.Legato; // 连奏滑音起点
                            }
                            if (nData.slideOutDown) {
                                note.slideOutType = M.SlideOutType.OutDown;  // 无尾滑出(下行)
                            }
                            if (nData.slideOutUp) {
                                note.slideOutType = M.SlideOutType.OutUp;    // 无尾滑出(上行)
                            }
                            if (nData.slideInBelow) {
                                note.slideInType = M.SlideInType.IntoFromBelow;  // 无头滑入(上行)
                            }
                            if (nData.slideInAbove) {
                                note.slideInType = M.SlideInType.IntoFromAbove;  // 无头滑入(下行)
                            }
                            if (nData.slideIn && lastByString.has(nData.string)) {
                                // 滑弦终点：按品位方向决定滑入上/下
                                const prev = lastByString.get(nData.string);
                                note.slideInType = nData.fret >= prev.fret
                                    ? M.SlideInType.IntoFromBelow
                                    : M.SlideInType.IntoFromAbove;
                            }
                            beat.addNote(note);
                            lastByString.set(nData.string, note);
                        }
                    }
                    voice.addBeat(beat);
                }
                const lastBeat = beats[beats.length - 1];
                prevNotes = (!lastBeat.rest && lastBeat.notes && lastBeat.notes.length > 0)
                    ? voice.beats[voice.beats.length - 1].notes
                    : null;
            }
            bar.addVoice(voice);
            staff.addBar(bar);
        }
        if (measures.length === 0) {
            // 至少保证一个小节，否则文件不合法
            const bar = new M.Bar();
            bar.keySignature = 0;
            bar.keySignatureType = M.KeySignatureType.Major;
            bar.addVoice(new M.Voice());
            staff.addBar(bar);
        }
    }
    return score;
}

function main() {
    const [specPath, outPath] = process.argv.slice(2);
    if (!specPath || !outPath) {
        console.error("用法: node alphatab_writer.js spec.json 输出.gp");
        process.exit(1);
    }
    const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
    const score = buildScore(spec);

    const settings = new alphaTab.Settings();

    // 每个小节对应一个 MasterBar（索引对齐），否则 finish/导出会崩溃
    const totalBars = score.tracks.reduce((n, t) => Math.max(n, t.staves[0].bars.length), 0);
    const num = (spec.timeSignature && spec.timeSignature.numerator) || 4;
    const den = (spec.timeSignature && spec.timeSignature.denominator) || 4;
    for (let i = 0; i < totalBars; i++) {
        const mb = new M.MasterBar();
        mb.timeSignatureNumerator = num;
        mb.timeSignatureDenominator = den;
        score.addMasterBar(mb);
    }
    // 第一个 MasterBar 上挂 tempo automation
    const tempoAutomation = new M.Automation();
    tempoAutomation.isLinear = false;
    tempoAutomation.type = M.AutomationType.Tempo;
    tempoAutomation.value = score.tempo;
    score.masterBars[0].tempoAutomations.push(tempoAutomation);

    score.finish(settings);

    const exporter = new alphaTab.exporter.Gp7Exporter();
    const data = exporter.export(score, settings);
    fs.writeFileSync(outPath, Buffer.from(data));
    console.log("WROTE", outPath, data.length);
}

main();
