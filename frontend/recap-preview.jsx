import { useEffect, useState } from "react";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";
import { toast } from "./notifications";

const selectStyle = "h-9 rounded-md border border-input bg-background pl-3 text-sm";

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Preview request failed.");
  return data;
}

export function RecapPreview() {
  const params = new URLSearchParams(window.location.search);
  const [savedPreview, setSavedPreview] = useState(() => params.get("preview") ?? "");
  const [catalog, setCatalog] = useState([]);
  const [series, setSeries] = useState(() => params.get("series") ?? "");
  const [chapter, setChapter] = useState(() => params.get("chapter") ?? "");
  const [endChapter, setEndChapter] = useState(() => params.get("end_chapter") ?? params.get("chapter") ?? "");
  const [reload, setReload] = useState(0);
  const [details, setDetails] = useState(null);
  const [error, setError] = useState("");
  const [progressId, setProgressId] = useState(null);
  const [percent, setPercent] = useState(0);

  useEffect(() => {
    function refresh(event) {
      if (event.detail === "preview") setReload(value => value + 1);
    }
    window.addEventListener("bliss-batch-complete", refresh);
    return () => window.removeEventListener("bliss-batch-complete", refresh);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    request("/api/preview/series", { signal: controller.signal }).then(setCatalog)
      .catch(err => { if (!controller.signal.aborted) toast.error(err.message); });
    return () => controller.abort();
  }, []);

  const query = new URLSearchParams({ series, chapter, end_chapter: endChapter }).toString();
  useEffect(() => {
    if (!chapter || savedPreview) return;
    const controller = new AbortController();
    request(`/api/preview/chapter?${query}`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setDetails(result); })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [chapter, query, savedPreview, reload]);

  useEffect(() => {
    if (!progressId) return;
    const controller = new AbortController();
    let timer;
    async function poll() {
      try {
        const current = await request(`/api/preview/progress/${progressId}`, {
          signal: controller.signal, cache: "no-store",
        });
        if (!controller.signal.aborted && Number.isInteger(current.percent)) setPercent(current.percent);
      } catch {
        // The render request reports failures separately.
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 500);
      }
    }
    poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [progressId]);

  function select(nextSeries, nextChapter, nextEnd = nextChapter) {
    if (savedPreview) {
      const url = new URL(window.location.href);
      for (const name of ["preview", "series", "chapter", "end_chapter"]) url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    }
    setSavedPreview("");
    setDetails(null);
    setError("");
    setSeries(nextSeries);
    setChapter(nextChapter);
    setEndChapter(nextEnd);
  }

  async function generate() {
    const id = crypto.randomUUID();
    setProgressId(id);
    setPercent(0);
    setError("");
    try {
      setDetails(await request("/api/preview/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series, chapter, end_chapter: endChapter, key: details.key, progress_id: id }),
      }));
      setPercent(100);
    } catch (err) {
      setError(err.message);
    } finally {
      setProgressId(null);
    }
  }

  const chapters = catalog.find(item => item.name === series)?.chapters ?? [];
  const contiguous = catalog.find(item => item.name === series)?.ranges.find(range => range.includes(chapter)) ?? [];
  const endOptions = contiguous.slice(Math.max(0, contiguous.indexOf(chapter)));
  const videoKey = /^[a-f0-9]{64}$/.test(savedPreview) ? savedPreview : details?.ready ? details.key : "";
  const video = videoKey && series && chapter ? `/api/preview/video/${videoKey}?${query}` : "";
  return (
    <section className="grid max-w-4xl gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-2">
          <Label htmlFor="preview-series">Series</Label>
          <NativeSelect id="preview-series" className={selectStyle} value={series} disabled={!!progressId}
            onChange={event => select(event.target.value, "")}>
            <option value="">Select series</option>
            {catalog.map(item => <option key={item.name}>{item.name}</option>)}
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="preview-chapter">From</Label>
          <NativeSelect id="preview-chapter" className={selectStyle} value={chapter} disabled={!series || !!progressId}
            onChange={event => {
              const nextChapter = event.target.value;
              const keepEnd = nextChapter && endChapter !== chapter && contiguous.includes(nextChapter) && contiguous.indexOf(endChapter) >= contiguous.indexOf(nextChapter);
              select(series, nextChapter, keepEnd ? endChapter : nextChapter);
            }}>
            <option value="">Select chapter</option>
            {chapters.map(item => <option key={item}>{item}</option>)}
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="preview-end-chapter">To</Label>
          <NativeSelect id="preview-end-chapter" className={selectStyle} value={endChapter} disabled={!chapter || !!progressId}
            onChange={event => select(series, chapter, event.target.value)}>
            {!chapter && <option value="">Select chapter</option>}
            {endOptions.map(item => <option key={item}>{item}</option>)}
          </NativeSelect>
        </div>
        <Button type="button" disabled={!details || !!progressId || !!video} onClick={generate}>
          Generate Preview
        </Button>
        {video && <Button asChild variant="outline"><a href={`${video}&download=true`} download>Download</a></Button>}
      </div>
      {progressId && (
        <p role="status" aria-label="Preview progress" className="text-sm text-muted-foreground">{percent}%</p>
      )}
      {error && <p role="alert" className="text-sm text-muted-foreground">{error}</p>}
      {video && (
        <video key={video} src={video} controls playsInline preload="metadata"
          className="aspect-video max-h-[72dvh] w-full rounded-md bg-black"
          onError={() => setError("The preview could not play. Reload Preview and try again.")} />
      )}
    </section>
  );
}
