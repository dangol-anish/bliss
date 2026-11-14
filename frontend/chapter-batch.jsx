import { useRef, useState } from "react";
import { ChapterSelect } from "./stitch.jsx";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { toast } from "./notifications";
import { batchRequest, batchSections, runBatchItem } from "./chapter-batch.js";

function itemName({ series, chapter, end_chapter }) {
  return `${series} · ${chapter}${end_chapter && end_chapter !== chapter ? ` to ${end_chapter}` : ""}`;
}

export function ChapterBatch({ section, buttonLabel = "Batch", disabled = false }) {
  const config = batchSections[section];
  const dialog = useRef(null);
  const stop = useRef(false);
  const running = useRef(false);
  const nextId = useRef(1);
  const [catalog, setCatalog] = useState([]);
  const [rows, setRows] = useState([{ id: 0, series: "", chapter: "", end_chapter: "" }]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState([]);
  const [minimum, setMinimum] = useState(1200);
  const [maximum, setMaximum] = useState(3000);

  async function open() {
    dialog.current.showModal();
    try {
      const next = await batchRequest(`/api/${config.catalog}/series`);
      setCatalog(next);
      setRows(current => current.map(row => {
        const chapters = next.find(item => item.name === row.series)?.chapters;
        if (!chapters) return { ...row, series: "", chapter: "", end_chapter: "" };
        if (!chapters.includes(row.chapter)) return { ...row, chapter: "", end_chapter: "" };
        return chapters.includes(row.end_chapter) ? row : { ...row, end_chapter: "" };
      }));
    } catch (failure) {
      setError(failure.message);
    }
  }

  function update(id, values) {
    setRows(current => current.map(row => row.id === id ? { ...row, ...values } : row));
  }

  function start(action) {
    if (running.current) return;
    const items = new Map();
    for (const { series, chapter, end_chapter } of rows) {
      const chapters = catalog.find(entry => entry.name === series)?.chapters ?? [];
      const first = chapters.indexOf(chapter);
      const last = chapters.indexOf(end_chapter || chapter);
      if (first < 0 || last < first) {
        setError("Choose a valid series and range in every row.");
        return;
      }
      const ranges = config.perChapter
        ? chapters.slice(first, last + 1).map(chapter => ({ series, chapter }))
        : [{ series, chapter, end_chapter: chapters[last] }];
      for (const range of ranges) items.set(JSON.stringify(range), range);
    }
    const queue = [...items.values()];
    if (action === "narrate") {
      const seriesOrder = [...new Set(queue.map(item => item.series))];
      const rank = new Map(catalog.map(entry => [entry.name, new Map(entry.chapters.map((name, index) => [name, index]))]));
      queue.sort((a, b) => seriesOrder.indexOf(a.series) - seriesOrder.indexOf(b.series)
        || rank.get(a.series).get(a.chapter) - rank.get(b.series).get(b.chapter));
    }
    if (action === "split" && (!Number.isInteger(minimum) || !Number.isInteger(maximum)
      || minimum < 1 || maximum < minimum || maximum > 100000)) {
      setError("Enter valid minimum and maximum heights.");
      return;
    }
    setResults([]);
    stop.current = false;
    advance(action, queue, 0);
  }

  async function advance(action, queue, index) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    let current = queue[index];
    try {
      if (action === "backup") {
        setProgress("Choose a backup folder…");
        const result = await batchRequest("/api/backup/batch", { entries: queue });
        if (result) {
          setResults(result.map(item => ({ label: item.file, detail: item.location,
            checkpoint: item.checkpoint })));
          setRows([{ id: nextId.current++, series: "", chapter: "", end_chapter: "" }]);
          setCatalog(await batchRequest("/api/stitch/series"));
          toast.success("Chapters moved to backup");
        }
        return;
      }
      for (; index < queue.length && !stop.current; index++) {
        current = queue[index];
        const label = itemName(current);
        const report = value => setProgress(`${index + 1}/${queue.length} · ${label} · ${Math.floor(value)}%`);
        report(0);
        const result = await runBatchItem(action, current, { minimum, maximum }, report, () => stop.current);
        if (result?.watch) setResults(previous => [...previous, { label, ...result }]);
      }
      if (stop.current) toast.info("Batch stopped");
      else toast.success("Batch complete");
    } catch (failure) {
      if (action === "narrate" && !stop.current && !failure.alertPlayed) {
        batchRequest("/api/narration/alert", {}).catch(() => {});
      }
      setError(action === "backup" ? failure.message : `${itemName(current)} — ${failure.message}`);
    } finally {
      running.current = false;
      setBusy(false);
      setStopping(false);
      setProgress("");
      window.dispatchEvent(new CustomEvent("bliss-batch-complete", { detail: section }));
    }
  }

  function close(event) {
    if (running.current) {
      event?.preventDefault();
      return;
    }
    dialog.current.close();
  }

  return (
    <>
      <Button type="button" variant="outline" disabled={disabled} onClick={open}>{buttonLabel}</Button>
      <dialog ref={dialog} aria-labelledby={`${section}-batch-title`} onCancel={close}
        className="fixed inset-0 m-auto max-h-[85dvh] w-[calc(100%-2rem)] max-w-2xl overflow-auto rounded-xl border border-white/15 bg-neutral-950 p-4 text-foreground backdrop:bg-black/75">
        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <h2 id={`${section}-batch-title`} className="text-sm font-semibold">Batch chapters</h2>
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => close()}>Close</Button>
          </div>
          {rows.map(row => {
            const entry = catalog.find(item => item.name === row.series);
            const chapters = entry?.chapters ?? [];
            const range = entry?.ranges?.find(group => group.includes(row.chapter)) ?? chapters;
            const id = `${section}-batch-${row.id}`;
            return (
              <div key={row.id} className="grid grid-cols-2 items-end gap-2 border-b border-white/10 pb-3 sm:grid-cols-[2fr_1fr_1fr_auto]">
                <ChapterSelect id={`${id}-series`} label="Series" placeholder="Choose series"
                  options={catalog.map(item => item.name)} value={row.series} disabled={busy}
                  onChange={event => update(row.id, { series: event.target.value, chapter: "", end_chapter: "" })} />
                <ChapterSelect id={`${id}-from`} label="From" placeholder="Choose chapter"
                  options={chapters} value={row.chapter} disabled={busy || !row.series}
                  onChange={event => update(row.id, { chapter: event.target.value, end_chapter: "" })} />
                <ChapterSelect id={`${id}-to`} label="To" placeholder={row.chapter || "Choose chapter"}
                  options={row.chapter ? range.slice(range.indexOf(row.chapter)) : []}
                  value={row.end_chapter} disabled={busy || !row.chapter}
                  onChange={event => update(row.id, { end_chapter: event.target.value })} />
                {rows.length > 1 && <Button type="button" variant="ghost" disabled={busy}
                  onClick={() => setRows(current => current.filter(item => item.id !== row.id))}>Remove</Button>}
              </div>
            );
          })}
          {section === "split-strip" && <div className="grid grid-cols-2 gap-2">
            <div className="grid gap-2"><Label htmlFor="batch-min">Min height (px)</Label>
              <Input id="batch-min" type="number" min={1} max={100000} value={minimum} disabled={busy}
                onChange={event => setMinimum(Number(event.target.value))} /></div>
            <div className="grid gap-2"><Label htmlFor="batch-max">Max height (px)</Label>
              <Input id="batch-max" type="number" min={1} max={100000} value={maximum} disabled={busy}
                onChange={event => setMaximum(Number(event.target.value))} /></div>
          </div>}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" disabled={busy || !catalog.length} onClick={() => {
              const id = nextId.current++;
              setRows(current => [...current, { id, series: "", chapter: "", end_chapter: "" }]);
            }}>Add Series</Button>
            {config.actions.map(([action, label]) => <Button type="button" key={action}
              disabled={busy || rows.some(row => !row.chapter)} onClick={() => start(action)}>{label}</Button>)}
            {busy && section !== "backup" && <Button type="button" variant="ghost"
              disabled={stopping} onClick={() => { stop.current = true; setStopping(true); }}>
              {stopping ? "Stopping…" : "Stop Batch"}</Button>}
          </div>
          {progress && <p role="status" className="text-sm text-muted-foreground">{progress}</p>}
          {error && <p role="alert" className="text-sm">{error}</p>}
          {results.map(item => <div key={item.watch ?? item.label} className="grid gap-1 text-sm">
            <span>{item.label}</span>
            {item.detail && <span className="break-all text-muted-foreground">{item.detail}</span>}
            {item.checkpoint && <span className="text-muted-foreground">Story saved through {item.checkpoint}.</span>}
            {item.watch && <div className="flex gap-2">
              <Button asChild variant="outline" size="sm"><a href={item.watch} target="_blank" rel="noreferrer">Watch</a></Button>
              <Button asChild variant="outline" size="sm"><a href={item.download} download>Download</a></Button>
            </div>}
          </div>)}
        </div>
      </dialog>
    </>
  );
}
