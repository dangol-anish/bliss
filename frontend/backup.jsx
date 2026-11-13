import { useEffect, useState } from "react";
import { ChapterSelect } from "./stitch.jsx";
import { Button } from "./ui/button";
import { toast } from "./notifications.js";

async function request(path, options) {
  const response = await fetch(path, options);
  if (response.status === 204) return;
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "Backup request failed.");
  return result;
}

export function Backup() {
  const [catalog, setCatalog] = useState([]);
  const [series, setSeries] = useState("");
  const [chapter, setChapter] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [backup, setBackup] = useState(null);
  const chapters = catalog.find(item => item.name === series)?.chapters ?? [];
  const endOptions = chapter ? chapters.slice(chapters.indexOf(chapter)) : [];

  useEffect(() => {
    const controller = new AbortController();
    request("/api/stitch/series", { signal: controller.signal, cache: "no-store" })
      .then(setCatalog)
      .catch(error => { if (!controller.signal.aborted) toast.error(error.message); });
    return () => controller.abort();
  }, []);

  async function create() {
    setBusy(true);
    setBackup(null);
    try {
      const result = await request("/api/backup/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series, chapter, end_chapter: end || chapter }),
      });
      if (!result) return;
      setBackup(result);
      setChapter("");
      setEnd("");
      toast.success("Chapters moved to backup");
      setCatalog(await request("/api/stitch/series", { cache: "no-store" }));
    } catch (error) {
      toast.error(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="grid max-w-lg gap-4">
      <ChapterSelect id="backup-series" label="Manhwa" options={catalog.map(item => item.name)}
        value={catalog.some(item => item.name === series) ? series : ""} placeholder="Select manhwa" disabled={busy || !catalog.length}
        onChange={event => {
          setSeries(event.target.value);
          setChapter("");
          setEnd("");
          setBackup(null);
        }} />
      <div className="grid gap-2 sm:grid-cols-2">
        <ChapterSelect id="backup-from" label="From" options={chapters} value={chapter}
          placeholder="Select chapter" disabled={busy || !chapters.length}
          onChange={event => {
            setChapter(event.target.value);
            setEnd("");
            setBackup(null);
          }} />
        <ChapterSelect id="backup-to" label="To" options={endOptions} value={end}
          placeholder={chapter || "Select chapter"} disabled={busy || !chapter}
          onChange={event => { setEnd(event.target.value); setBackup(null); }} />
      </div>
      <div>
        <Button type="button" disabled={busy || !chapter} onClick={create}>
          {busy ? "Moving to backup…" : "Move to Backup"}
        </Button>
      </div>
      {backup && (
        <p role="status" className="break-words text-sm text-muted-foreground">
          {backup.file}<br />
          {backup.location}<br />
          {backup.checkpoint ? `Story saved through ${backup.checkpoint}.` : "No accepted story checkpoint for this range."}
        </p>
      )}
    </section>
  );
}
