import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { Input, fieldControlClass } from "./ui/input";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";
import { toast } from "./notifications";

export function SplitStrip() {
  const [catalog, setCatalog] = useState([]);
  const [series, setSeries] = useState("");
  const [chapter, setChapter] = useState("");
  const [minimum, setMinimum] = useState("1200");
  const [maximum, setMaximum] = useState("3000");
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState("");
  const viewport = useRef(null);
  const preview = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/panels/series", { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error("Could not load stitched chapters.");
        return response.json();
      })
      .then(setCatalog)
      .catch(error => { if (!controller.signal.aborted) toast.error(error.message); });
    return () => controller.abort();
  }, []);

  async function run(action) {
    setBusy(action);
    if (action === "preview") setPlan(null);
    try {
      const response = await fetch(`/api/split/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series, chapter, minimum: Number(minimum), maximum: Number(maximum),
          preview_id: action === "save" ? plan.id : null }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the chapter and heights.");
      setPlan(result);
      if (action === "preview") viewport.current?.scrollTo(0, 0);
      if (action === "save") toast.success(`Saved ${result.sections.length} images`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setBusy("");
    }
  }

  async function openFolder() {
    try {
      const response = await fetch("/api/split/open-folder", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ series, chapter, revision: plan.id.slice(0, 16) }),
      });
      if (!response.ok) throw new Error("Could not open the split image folder.");
    } catch (error) {
      toast.error(error.message);
    }
  }

  const chapters = catalog.find(item => item.name === series)?.chapters ?? [];
  const exceptions = plan?.sections.filter(section => section.outside_limits).length ?? 0;
  const query = new URLSearchParams({ series, chapter, source: plan?.source ?? "" });
  return (
    <section className="flex h-full min-h-0 flex-col gap-4">
      <form className="flex flex-wrap items-end gap-x-3 gap-y-2" onSubmit={event => { event.preventDefault(); run("preview"); }}>
        <div className="grid min-w-0 max-w-64 gap-1">
          <Label htmlFor="split-series">Series</Label>
          <NativeSelect id="split-series" value={series} disabled={!!busy} className={fieldControlClass + " pr-8"}
            onChange={event => { setSeries(event.target.value); setChapter(""); setPlan(null); }}>
            <option value="">Choose series</option>
            {catalog.map(item => <option key={item.name}>{item.name}</option>)}
          </NativeSelect>
        </div>
        <div className="grid min-w-0 max-w-64 gap-1">
          <Label htmlFor="split-chapter">Chapter</Label>
          <NativeSelect id="split-chapter" value={chapter} disabled={!series || !!busy} className={fieldControlClass + " pr-8"}
            onChange={event => { setChapter(event.target.value); setPlan(null); }}>
            <option value="">Choose chapter</option>
            {chapters.map(name => <option key={name}>{name}</option>)}
          </NativeSelect>
        </div>
        <div className="grid w-28 gap-1">
          <Label htmlFor="split-minimum">Min height (px)</Label>
          <Input id="split-minimum" type="number" min={1} max={100000} required value={minimum} disabled={!!busy}
            onChange={event => { setMinimum(event.target.value); setPlan(null); }} />
        </div>
        <div className="grid w-28 gap-1">
          <Label htmlFor="split-maximum">Max height (px)</Label>
          <Input id="split-maximum" type="number" min={1} max={100000} required value={maximum} disabled={!!busy}
            onChange={event => { setMaximum(event.target.value); setPlan(null); }} />
        </div>
        <Button disabled={!chapter || !!busy}>{busy === "preview" ? "Finding cuts…" : "Preview Cuts"}</Button>
        {plan && (
          <Button type="button" variant="outline" disabled={!!busy}
            onClick={plan.folder ? openFolder : () => run("save")}>
            {plan.folder ? "Open Folder" : busy === "save" ? "Saving…" : "Save Images"}
          </Button>
        )}
      </form>
      {plan && (
        <div className="flex flex-wrap items-center gap-3 border-t border-white/10 pt-3 text-xs">
          <span role="status">{plan.sections.length} images{plan.folder ? " saved" : ""}</span>
          {exceptions > 0 && <span className="text-yellow-300">{exceptions} outside the height range</span>}
          <NativeSelect aria-label="Jump to image" className="h-8 rounded-md border border-input bg-background pl-2 text-xs"
            defaultValue="" key={plan.id} onChange={event => {
              const section = plan.sections[Number(event.target.value)];
              if (section && event.target.value !== "") {
                viewport.current.scrollTop = section.top / plan.height * preview.current.offsetHeight;
              }
            }}>
            <option value="">Jump to image</option>
            {plan.sections.map((section, index) => <option value={index} key={section.file}>
              {section.file} · {section.height} px{section.outside_limits ? " · outside range" : ""}
            </option>)}
          </NativeSelect>
        </div>
      )}
      <div ref={viewport} tabIndex={plan ? 0 : undefined} aria-label="Strip preview"
        className="min-h-0 flex-1 overflow-auto [scrollbar-width:none]">
        {plan && (
          <div ref={preview} className="relative mx-auto w-[360px] max-w-full">
            {Array.from({ length: Math.ceil(plan.height / plan.tile_height) }, (_, index) => (
              <img key={index} src={`/api/panels/tile/${index}?${query}`} alt="" loading="lazy"
                width={plan.width} height={Math.min(plan.tile_height, plan.height - index * plan.tile_height)}
                className="block h-auto w-full" />
            ))}
            {plan.boxes.map(([left, top, right, bottom], index) => (
              <div key={index} className="pointer-events-none absolute border-2 border-yellow-300 shadow-[0_0_0_1px_black]"
                style={{ left: `${left / plan.width * 100}%`, top: `${top / plan.height * 100}%`,
                  width: `${(right - left) / plan.width * 100}%`, height: `${(bottom - top) / plan.height * 100}%` }}>
                <span className="absolute left-0 top-0 bg-black px-1 text-[10px] text-yellow-300">P{String(index + 1).padStart(3, "0")}</span>
              </div>
            ))}
            {plan.sections.slice(0, -1).map((section, index) => (
              <div key={section.file} className="pointer-events-none absolute inset-x-0 z-10 border-t-2 border-dashed border-cyan-300 shadow-[0_-1px_black]"
                style={{ top: `${section.bottom / plan.height * 100}%` }}>
                <span className="absolute right-0 top-0 bg-black px-1 text-[10px] text-cyan-300">{plan.sections[index + 1].file}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
