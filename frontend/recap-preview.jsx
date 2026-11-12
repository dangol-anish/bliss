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
  const [details, setDetails] = useState(null);
  const [error, setError] = useState("");
  const [progressId, setProgressId] = useState(null);
  const [percent, setPercent] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    request("/api/panels/series", { signal: controller.signal }).then(setCatalog)
      .catch(err => { if (!controller.signal.aborted) toast.error(err.message); });
    return () => controller.abort();
  }, []);

  const query = new URLSearchParams({ series, chapter }).toString();
  useEffect(() => {
    if (!chapter || savedPreview) return;
    const controller = new AbortController();
    request(`/api/preview/chapter?${query}`, { signal: controller.signal })
      .then(setDetails)
      .catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [chapter, query, savedPreview]);

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

  function select(nextSeries, nextChapter) {
    if (savedPreview) {
      const url = new URL(window.location.href);
      for (const name of ["preview", "series", "chapter"]) url.searchParams.delete(name);
      window.history.replaceState(null, "", url);
    }
    setSavedPreview("");
    setDetails(null);
    setError("");
    setSeries(nextSeries);
    setChapter(nextChapter);
  }

  async function generate() {
    const id = crypto.randomUUID();
    setProgressId(id);
    setPercent(0);
    setError("");
    try {
      setDetails(await request("/api/preview/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series, chapter, key: details.key, progress_id: id }),
      }));
      setPercent(100);
    } catch (err) {
      setError(err.message);
    } finally {
      setProgressId(null);
    }
  }

  const chapters = catalog.find(item => item.name === series)?.chapters ?? [];
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
          <Label htmlFor="preview-chapter">Chapter</Label>
          <NativeSelect id="preview-chapter" className={selectStyle} value={chapter} disabled={!series || !!progressId}
            onChange={event => select(series, event.target.value)}>
            <option value="">Select chapter</option>
            {chapters.map(item => <option key={item}>{item}</option>)}
          </NativeSelect>
        </div>
        <Button type="button" disabled={!details || !!progressId || !!video} onClick={generate}>
          Generate Preview
        </Button>
        {video && <Button asChild variant="outline"><a href={`${video}&download=true`} download>Download</a></Button>}
      </div>
      {progressId && (
        <div role="status" className="flex max-w-xs items-center gap-3 text-sm text-muted-foreground">
          <progress aria-label="Preview progress" value={percent} max={100} className="w-full accent-white" />
          <span>{percent}%</span>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-muted-foreground">{error}</p>}
      {video && (
        <video key={video} src={video} controls playsInline preload="metadata"
          className="aspect-video max-h-[72dvh] w-full rounded-md bg-black"
          onError={() => setError("The preview could not play. Reload this chapter and try again.")} />
      )}
    </section>
  );
}
