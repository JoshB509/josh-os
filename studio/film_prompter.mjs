// Select a script from Camera Hub's own library on the Elgato Prompter through Camera Hub's local control connection, then read
// everything back. Used by studio/filming.py. Never writes scripts, recording or power.
//   node film_prompter.mjs status
//   node film_prompter.mjs select <library script GUID> <its chapter count> <Prompter device id>
// Prints one JSON line: {ok, ...} or {ok:false, error}.
import {connectCameraHub} from 'file:///C:/Users/Anigma%20PC/Documents/Stream%20Deck%20Studio/launchers/camera-hub-rpc.mjs';

const [cmd, guid, wantChapters, device] = process.argv.slice(2);
const TEXT_MODE = 1;
const out = o => console.log(JSON.stringify(o));
const sleep = ms => new Promise(r => setTimeout(r, ms));

let c;
try {
  c = await connectCameraHub();
} catch (e) {
  out({ok: false, error: 'Camera Hub is not open or its control connection is not ready.'});
  process.exit(0);
}
try {
  const prompters = (await c.call('getAvailablePrompter')) || [];
  const ours = prompters.filter(d => d.isPrompter && (!device || d.deviceID === device));
  if (cmd === 'status') {
    const sel = await c.call('getPrompterSelectedTextSource');
    const mode = await c.call('getPrompterProperty', {propertyID: 1});
    out({ok: true, prompters: prompters.length, ours: ours.length, selected: sel?.uniqueId || '', mode: mode?.value});
  } else if (cmd === 'select') {
    // Camera Hub's calls don't say which Prompter they act on, so with two connected Josh OS can't be sure. It stops.
    const connected = prompters.filter(d => d.isPrompter);
    if (connected.length !== 1) throw Error(`${connected.length} Prompters are connected. Josh OS only sets the script when exactly one is, so it can be sure which one shows it.`);
    if (ours.length !== 1) throw Error('The connected Prompter is not the one configured in the Stream Deck controls.');
    const sources = (await c.call('getPrompterTextSources')) || [];
    if (!sources.some(s => s.uniqueId === guid)) throw Error("That script isn't in Camera Hub's library. Save it in Prompter > Scripts first.");
    await c.call('setPrompterTextSource', {uniqueId: guid});
    const mode = await c.call('getPrompterProperty', {propertyID: 1});
    if (mode?.value !== TEXT_MODE) await c.call('setPrompterProperty', {propertyID: 1, value: TEXT_MODE});
    await sleep(400);
    const sel = await c.call('getPrompterSelectedTextSource');
    const after = await c.call('getPrompterProperty', {propertyID: 1});
    const count = await c.call('getPrompterChapterCount');
    const shown = await c.call('getPrompterDisplayedChapter').catch(() => null);
    if (sel?.uniqueId !== guid) throw Error('Camera Hub did not select the script.');
    if (after?.value !== TEXT_MODE) throw Error('The Prompter did not switch to text mode.');
    if (Number(count?.value) !== Number(wantChapters)) throw Error(`Camera Hub shows ${count?.value} parts, but the saved script has ${wantChapters}.`);
    out({ok: true, selected: sel.uniqueId, mode: after.value, chapters: count.value, displayed: shown?.value ?? null, device: ours[0].deviceID});
  } else {
    throw Error('Unknown command');
  }
} catch (e) {
  out({ok: false, error: String(e.message || e).slice(0, 300)});
} finally {
  c?.close();
}
