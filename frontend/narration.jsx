import { useEffect, useState } from "react";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";
import { toast } from "./notifications";

const selectStyle = "h-9 rounded-md border border-input bg-background pl-3 text-sm";

async function request(path, body, signal) {
  const response = await fetch(path, {
    ...(body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
    signal,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Narration request failed.");
  return data;
}

export function Narration() {
  const [catalog, setCatalog] = useState([]);
  const [series, setSeries] = useState("");
  const [chapter, setChapter] = useState("");
  const [revision, setRevision] = useState("");
  const [details, setDetails] = useState(null);
  const [job, setJob] = useState(null);
  const [connected, setConnected] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    function refresh(event) {
      if (event.detail === "narration") setReload(value => value + 1);
    }
    window.addEventListener("bliss-batch-complete", refresh);
    return () => window.removeEventListener("bliss-batch-complete", refresh);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    request("/api/narration/series", undefined, controller.signal).then(setCatalog)
      .catch(err => { if (!controller.signal.aborted) toast.error(err.message); });
    return () => controller.abort();
  }, []);

  const running = !!job?.running;
  useEffect(() => {
    let stopped = false;
    let timer;
    const controller = new AbortController();
    async function poll() {
      try {
        const [next, connection] = await Promise.all([
          request("/api/narration/job", undefined, controller.signal),
          request("/api/bliss-studio/status", undefined, controller.signal),
        ]);
        if (stopped) return;
        setJob(next);
        setConnected(connection.connected);
        if (connection.connected) setCode("");
      } catch {
        if (!stopped) setConnected(false);
      } finally {
        if (!stopped) timer = setTimeout(poll, 1500);
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, []);

  useEffect(() => {
    if (!chapter || running) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ series, chapter });
    if (revision) query.set("revision", revision);
    request(`/api/narration/chapter?${query}`, undefined, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setDetails(value);
      setError("");
    }).catch(err => {
      if (controller.signal.aborted) return;
      setDetails(null);
      setError(err.message);
    });
    return () => controller.abort();
  }, [series, chapter, revision, running, reload]);

  async function action(path, body, after) {
    setBusy(true);
    try {
      after?.(await request(path, body));
    } catch (err) {
      toast.error(err.message);
      if (path === "/api/narration/start") request("/api/narration/alert", {}).catch(() => {});
    } finally {
      setBusy(false);
    }
  }

  function selectChapter(nextSeries, nextChapter) {
    setSeries(nextSeries);
    setChapter(nextChapter);
    setRevision("");
    setDetails(null);
    setError("");
    setRetry(false);
  }

  const chapters = catalog.find(item => item.name === series)?.chapters ?? [];
  const payload = details && { series, chapter, revision: details.revision, key: details.key, retry };
  const finished = details && details.completed === details.total;
  const current = job?.key === details?.key ? job : null;
  return (
    <section className="grid max-w-4xl gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid max-w-64 gap-1">
          <Label htmlFor="narration-series">Series</Label>
          <NativeSelect id="narration-series" value={series} className={selectStyle} disabled={running || busy}
            onChange={event => selectChapter(event.target.value, "")}>
            <option value="">Choose series</option>
            {catalog.map(item => <option key={item.name}>{item.name}</option>)}
          </NativeSelect>
        </div>
        <div className="grid max-w-64 gap-1">
          <Label htmlFor="narration-chapter">Chapter</Label>
          <NativeSelect id="narration-chapter" value={chapter} className={selectStyle} disabled={!series || running || busy}
            onChange={event => selectChapter(series, event.target.value)}>
            <option value="">Choose chapter</option>
            {chapters.map(name => <option key={name}>{name}</option>)}
          </NativeSelect>
        </div>
        {details?.revisions.length > 1 && (
          <div className="grid gap-1">
            <Label htmlFor="narration-split">Saved split</Label>
            <NativeSelect id="narration-split" className={selectStyle} value={details.revision} disabled={running || busy}
              onChange={event => { setRevision(event.target.value); setDetails(null); setRetry(false); }}>
              {details.revisions.map((item, index) => <option key={item.id} value={item.id}>
                {item.images} images · {item.minimum}–{item.maximum} px{index === 0 ? " · latest" : ""}
              </option>)}
            </NativeSelect>
          </div>
        )}
      </div>
      {error && <p role="alert" className="text-sm text-yellow-300">{error}</p>}
      <div className="flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
        <span className="mr-1 text-sm text-muted-foreground">{connected ? "AI Studio connected" : "Connect AI Studio"}</span>
        {!connected && <>
          <Button variant="outline" size="sm" disabled={busy}
            onClick={() => action("/api/bliss-studio/open-folder", {})}>Open Extension Folder</Button>
          <Button variant="outline" size="sm" disabled={busy}
            onClick={() => action("/api/bliss-studio/code", {}, value => setCode(value.code))}>Get Pairing Code</Button>
        </>}
      </div>
      {code && <p className="text-sm">Paste this code into the Bliss extension on your AI Studio tab. <code className="select-all rounded bg-white/10 px-2 py-1">{code}</code></p>}
      {details && (
        <div className="grid gap-3">
          <p className="text-sm text-muted-foreground">
            {details.from_chapter ? `Continuing from ${details.from_chapter}` : "No earlier story checkpoint"}
            {" · "}{current?.completed ?? details.completed}/{details.total} batches saved
          </p>
          {details.submitted && !running && (
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={retry} onChange={event => setRetry(event.target.checked)} />
              I checked the last AI Studio request and want to send this batch again.
            </label>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {!details.accepted && !running && <Button disabled={(!finished && !connected) || busy || (details.submitted && !retry)}
              onClick={() => action("/api/narration/start", payload, () => {
                setJob({ key: details.key, running: true, completed: details.completed, message: "Connecting to AI Studio" });
                setRetry(false);
              })}>{finished ? "Save Narration" : details.completed || details.submitted ? "Resume Narration" : "Start Narration"}</Button>}
            {details.accepted && <span className="text-sm">Narration saved · checkpoint updated</span>}
          </div>
          {details.script && <div aria-label="Narration script"
            className="max-h-[55vh] overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-white/5 p-4 text-sm leading-relaxed">
            {details.script}
          </div>}
        </div>
      )}
      {running && <Button className="w-fit" variant="outline" disabled={busy}
        onClick={() => action("/api/narration/stop", {}, () => toast.info("Stopping narration"))}>Stop</Button>}
      {(running || (current && !details?.accepted)) && job?.message &&
        <p role="status" className="text-sm text-muted-foreground">{job.message}</p>}
      {error && chapter && <Button className="w-fit" variant="ghost" onClick={() => setReload(value => value + 1)}>Check Again</Button>}
    </section>
  );
}
