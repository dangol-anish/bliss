import { useRef, useState, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ChapterBatch } from "./chapter-batch.jsx";
import { Backup } from "./backup.jsx";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Narration } from "./narration.jsx";
import { RecapPreview } from "./recap-preview.jsx";
import { Panels } from "./panels.jsx";
import { SplitStrip } from "./split-strip.jsx";
import { Stitch } from "./stitch.jsx";

const workspaceClass = "min-w-0 flex-1 px-1 py-4 sm:px-5 sm:py-6";

function subscribeToStep(callback) {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

function currentStep() {
  const hash = window.location.hash;
  return ["#stitch", "#panels", "#split-strip", "#narration", "#preview", "#backup"].includes(hash) ? hash : "#downloader";
}

function DownloadRows() {
  const nextRow = useRef(1);
  const [rows, setRows] = useState([0]);

  return (
    <>
      {rows.map(id => (
        <fieldset key={id} data-download-row className="grid min-w-0 gap-2 border-b border-white/10 pb-3">
          <div className="flex items-end gap-2">
            <div className="grid min-w-0 flex-1 gap-2">
              <Label htmlFor={`series-url-${id}`}>Series link</Label>
              <Input id={`series-url-${id}`} name="url" inputMode="url" autoComplete="off"
                required maxLength={2048} placeholder="Webtoon or Weeb Central" />
            </div>
            {rows.length > 1 && (
              <Button type="button" variant="ghost" aria-label="Remove series"
                onClick={() => setRows(current => current.filter(row => row !== id))}>Remove</Button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="grid min-w-0 gap-2">
              <Label htmlFor={`chapter-start-${id}`}>From</Label>
              <Input id={`chapter-start-${id}`} name="start" type="number" min={1} step={1} placeholder="First" />
            </div>
            <div className="grid min-w-0 gap-2">
              <Label htmlFor={`chapter-end-${id}`}>To</Label>
              <Input id={`chapter-end-${id}`} name="end" type="number" min={1} step={1} placeholder="Last" />
            </div>
          </div>
        </fieldset>
      ))}
      <p className="text-xs text-muted-foreground">Positions count from the first listed chapter.</p>
      <Button type="button" variant="outline" className="justify-self-start" onClick={() => {
        const id = nextRow.current++;
        setRows(current => [...current, id]);
      }}>Add Series</Button>
    </>
  );
}

function DownloaderLayout() {
  const step = useSyncExternalStore(subscribeToStep, currentStep);
  const stitchSelected = step === "#stitch";
  const panelsSelected = step === "#panels";
  const previewSelected = step === "#preview";
  const narrationSelected = step === "#narration";
  const backupSelected = step === "#backup";
  const splitSelected = step === "#split-strip";

  return (
    <>
      <aside className="w-32 shrink-0 rounded-2xl border border-white/10 bg-neutral-950/90 p-2 backdrop-blur-xl sm:w-48 sm:p-3">
        <div className="px-2.5 py-3 text-sm font-semibold">Bliss</div>
        <nav aria-label="Workflow" className="grid gap-1">
          <Button asChild variant={step === "#downloader" ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#downloader" aria-current={step === "#downloader" ? "page" : undefined}>Downloader</a>
          </Button>
          <Button asChild variant={stitchSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#stitch" aria-current={stitchSelected ? "page" : undefined}>Stitch</a>
          </Button>
          <Button asChild variant={panelsSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#panels" aria-current={panelsSelected ? "page" : undefined}>Panels</a>
          </Button>
          <Button asChild variant={splitSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#split-strip" aria-current={splitSelected ? "page" : undefined}>Split Strip</a>
          </Button>
          <Button asChild variant={narrationSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#narration" aria-current={narrationSelected ? "page" : undefined}>Narration</a>
          </Button>
          <Button asChild variant={previewSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#preview" aria-current={previewSelected ? "page" : undefined}>Preview</a>
          </Button>
          <Button asChild variant={backupSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#backup" aria-current={backupSelected ? "page" : undefined}>Backup</a>
          </Button>
        </nav>
      </aside>
      <main hidden={step !== "#downloader"} aria-label="Downloader" className={workspaceClass}>
        <form id="download-form" className="grid max-w-lg gap-4">
          <fieldset id="download-rows" className="grid min-w-0 gap-4">
            <DownloadRows />
          </fieldset>
          <div className="flex flex-wrap gap-2">
            <Button type="submit">Download</Button>
            <Button type="button" id="open-folder" variant="outline">Open Folder</Button>
            <Button type="button" id="cancel-download" variant="ghost" disabled>Cancel</Button>
          </div>
          <p id="download-status" role="status" className="text-sm text-muted-foreground" />
        </form>
      </main>
      <main hidden={!panelsSelected} aria-label="Panels" className={`${workspaceClass} h-[calc(100dvh-4rem)] sm:h-[calc(100dvh-4.5rem)]`}>
        <Panels active={panelsSelected} />
      </main>
      {splitSelected && (
        <main aria-label="Split Strip" className={`${workspaceClass} h-[calc(100dvh-4rem)] sm:h-[calc(100dvh-4.5rem)]`}>
          <div className="mb-3"><ChapterBatch section="split-strip" /></div>
          <SplitStrip />
        </main>
      )}
      {narrationSelected && (
        <main aria-label="Narration" className={workspaceClass}>
          <div className="mb-3"><ChapterBatch section="narration" /></div>
          <Narration />
        </main>
      )}
      {previewSelected && (
        <main aria-label="Preview" className={workspaceClass}>
          <div className="mb-3"><ChapterBatch section="preview" /></div>
          <RecapPreview />
        </main>
      )}
      {backupSelected && (
        <main aria-label="Backup" className={workspaceClass}>
          <div className="mb-3"><ChapterBatch section="backup" /></div>
          <Backup />
        </main>
      )}
      {stitchSelected && (
        <main aria-label="Stitch" className={workspaceClass}>
          <div className="mb-3"><ChapterBatch section="stitch" /></div>
          <Stitch />
        </main>
      )}
    </>
  );
}

export function mountDownloader() {
  const root = createRoot(document.getElementById("app"));
  flushSync(() => root.render(<DownloaderLayout />));
}
