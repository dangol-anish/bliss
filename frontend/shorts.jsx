import { useEffect, useState } from "react";
import { batchRequest } from "./chapter-batch.js";
import { ChapterSelect } from "./stitch.jsx";
import { Button } from "./ui/button";
import { toast } from "./notifications";

export function Shorts() {
  const [catalog, setCatalog] = useState([]);
  const [initial] = useState(() => new URLSearchParams(window.location.search));
  const [series, setSeries] = useState(initial.get("shortSeries") ?? "");
  const [chapter, setChapter] = useState(initial.get("shortFrom") ?? "");
  const [end, setEnd] = useState(initial.get("shortTo") ?? "");
  const [job, setJob] = useState({});
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const row = catalog.find(item => item.name === series);
  const range = row?.ranges.find(group => group.includes(chapter)) ?? [];
  const query = new URLSearchParams({ series, chapter, end_chapter: end || chapter }).toString();
  const pending = busy || job.running;
  const selectedJob = job.selection?.series === series && job.selection.chapter === chapter
    && job.selection.end_chapter === (end || chapter);

  useEffect(() => {
    batchRequest("/api/preview/series").then(setCatalog).catch(failure => setError(failure.message));
  }, []);

  useEffect(() => {
    let current = true;
    const read = async () => {
      try {
        const next = await batchRequest("/api/shorts/job");
        if (current) setJob(next);
      } catch (failure) {
        if (current) setError(failure.message);
      }
    };
    read();
    const timer = setInterval(read, 1200);
    return () => { current = false; clearInterval(timer); };
  }, []);

  useEffect(() => {
    setResult(null);
    if (!chapter || pending) return;
    const controller = new AbortController();
    batchRequest(`/api/shorts/result?${query}`, undefined, controller.signal)
      .then(setResult).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [chapter, pending, query]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      await batchRequest("/api/shorts/start", { series, chapter, end_chapter: end || chapter });
      setJob({ running: true, message: "Finding a story" });
      setVersion(Date.now());
    } catch (failure) {
      toast.error(failure.message);
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  const video = `/api/shorts/video?${query}&v=${version}`;
  return <div className="grid gap-4">
    <div className="flex flex-wrap items-end gap-2">
      <ChapterSelect id="short-series" label="Series" placeholder="Choose series"
        options={catalog.map(item => item.name)} value={series} disabled={pending}
        onChange={event => { setSeries(event.target.value); setChapter(""); setEnd(""); setError(""); }} />
      <ChapterSelect id="short-from" label="From" placeholder="Choose chapter"
        options={row?.chapters ?? []} value={chapter} disabled={pending || !series}
        onChange={event => { setChapter(event.target.value); setEnd(""); setError(""); }} />
      <ChapterSelect id="short-to" label="To" placeholder={chapter || "Choose chapter"}
        options={range.slice(Math.max(0, range.indexOf(chapter)))} value={end} disabled={pending || !chapter}
        onChange={event => { setEnd(event.target.value); setError(""); }} />
      <Button onClick={start} disabled={!chapter || pending}>{result ? "Generate Again" : "Generate Short"}</Button>
      {job.running && <Button variant="ghost" onClick={() => batchRequest("/api/shorts/stop", {})
        .catch(failure => toast.error(failure.message))}>Stop</Button>}
    </div>
    {!catalog.length && !error && <p className="text-sm text-muted-foreground">Save a chapter in Narration first.</p>}
    {(job.running || job.message === "Stopped") && <p role="status" className="text-sm text-muted-foreground">
      {Number.isInteger(job.percent) ? `${job.percent}%` : job.message}
    </p>}
    {(error || (selectedJob && job.error)) && <p role="alert" className="text-sm">{error || job.error}</p>}
    {result && <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
      <div className="grid gap-2">
        <video key={video} controls playsInline preload="metadata" src={video}
          className="max-h-[70vh] w-full rounded-md bg-black" />
        <Button asChild variant="outline" className="justify-self-start">
          <a href={`${video}&download=true`} download>Download</a>
        </Button>
      </div>
      <div className="grid max-w-xl gap-3 text-sm">
        <h2 className="font-semibold">{result.title}</h2>
        <details>
          <summary className="cursor-pointer">Script &amp; panel cues</summary>
          {result.beats.map((beat, index) => <div className="mt-3 grid gap-1" key={index}>
            <p>{beat.narration}</p>
            <ul className="text-muted-foreground">{beat.shots.map(shot =>
              <li key={shot.cue}>{result.chapters[shot.panel_id.split("-")[0]]} · {shot.panel_id.split("-")[1]} · {shot.cue}</li>
            )}</ul>
          </div>)}
        </details>
      </div>
    </div>}
  </div>;
}
