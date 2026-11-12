import { useEffect, useState } from "react";
import { toast } from "./notifications.js";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";
import { fieldControlClass } from "./ui/input";
import { cn } from "./ui/cn.js";

function ChapterSelect({ id, label, options, placeholder, ...props }) {
  return (
    <div className="grid min-w-0 gap-2">
      <Label htmlFor={id}>{label}</Label>
      <NativeSelect id={id} className={cn(fieldControlClass, "pr-8", !props.value && "text-muted-foreground")} {...props}>
        <option value="">{placeholder}</option>
        {options.map((name) => <option key={name} value={name}>{name}</option>)}
      </NativeSelect>
    </div>
  );
}

export function Stitch() {
  const [catalog, setCatalog] = useState(null);
  const [series, setSeries] = useState("");
  const [chapter, setChapter] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState("");
  const [percent, setPercent] = useState(0);
  const chapters = catalog?.find((entry) => entry.name === series)?.chapters ?? [];
  const endOptions = chapter ? chapters.slice(chapters.indexOf(chapter)) : [];

  useEffect(() => {
    const controller = new AbortController();
    async function loadSeries() {
      try {
        const response = await fetch("/api/stitch/series", { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Could not load downloaded series.");
        setCatalog(await response.json());
      } catch (error) {
        if (!controller.signal.aborted) toast.error(error.message);
      }
    }
    loadSeries();
    return () => controller.abort();
  }, []);

  async function run(action) {
    if (busy || !series || !chapter) return;
    const progressId = action === "create" ? crypto.randomUUID() : undefined;
    const progress = new AbortController();
    let timer;
    setPercent(0);
    setBusy(action);

    async function updateProgress() {
      try {
        const response = await fetch(`/api/stitch/progress/${progressId}`, {
          signal: progress.signal,
        });
        if (response.ok) {
          const { percent: current } = await response.json();
          if (!progress.signal.aborted && Number.isInteger(current)) setPercent(current);
        }
      } catch {
        // The stitch request reports any failure.
      } finally {
        if (!progress.signal.aborted) timer = setTimeout(updateProgress, 500);
      }
    }

    if (progressId) timer = setTimeout(updateProgress, 500);
    try {
      const response = await fetch(`/api/stitch/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series, chapter, end_chapter: end || chapter, progress_id: progressId }),
      });
      if (response.status === 204) return;
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "The chapter operation failed.");
      if (action === "create") {
        toast.success(`Created ${result.chapters} strip ${result.chapters === 1 ? "PDF" : "PDFs"}`);
      } else if (result.renamed) {
        toast.success(`Renumbered ${result.chapters} ${result.chapters === 1 ? "chapter" : "chapters"}`);
      } else {
        toast.info("Pages are already numbered");
      }
    } catch (error) {
      toast.error(error.message);
    } finally {
      progress.abort();
      clearTimeout(timer);
      setBusy("");
    }
  }

  return (
    <div className="grid max-w-lg gap-4">
      <ChapterSelect id="stitch-series" label="Manhwa" options={catalog?.map(({ name }) => name) ?? []}
          value={series} placeholder={catalog?.length === 0 ? "No downloads" : "Select manhwa"}
          disabled={!!busy || !catalog?.length} onChange={(event) => {
            setSeries(event.target.value);
            setChapter("");
            setEnd("");
          }} />
      <fieldset className="min-w-0">
        <legend className="mb-3 text-sm font-medium">Chapter range</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          <ChapterSelect id="stitch-from" label="From" options={chapters} value={chapter}
            placeholder="Select chapter" disabled={!!busy || !chapters.length} onChange={(event) => {
              setChapter(event.target.value);
              setEnd("");
            }} />
          <ChapterSelect id="stitch-to" label="To" options={endOptions} value={end}
            placeholder={chapter || "Select chapter"} disabled={!!busy || !chapter}
            onChange={(event) => setEnd(event.target.value)} />
        </div>
      </fieldset>
      <section aria-label="Chapter actions" className="flex flex-wrap gap-2">
        <Button type="button" disabled={!!busy || !chapter} onClick={() => run("renumber")}>
          {busy === "renumber" ? "Renumbering…" : "Renumber"}
        </Button>
        <Button type="button" disabled={!!busy || !chapter} onClick={() => run("create")}>
          {busy === "create" ? "Stitching…" : "Stitch"}
        </Button>
        <Button type="button" variant="outline" disabled={!!busy || !chapter} onClick={() => run("open-folder")}>
          Open Folder
        </Button>
      </section>
      {busy === "create" && <p role="status" className="text-sm text-muted-foreground">{percent}%</p>}
    </div>
  );
}
