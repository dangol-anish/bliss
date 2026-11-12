import { useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Stitch } from "./stitch.jsx";

const workspaceClass = "min-w-0 flex-1 px-1 py-4 sm:px-5 sm:py-6";

function subscribeToStep(callback) {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

function isStitchSelected() {
  return window.location.hash === "#stitch";
}

function DownloaderLayout() {
  const stitchSelected = useSyncExternalStore(subscribeToStep, isStitchSelected);

  return (
    <>
      <aside className="w-32 shrink-0 rounded-2xl border border-white/10 bg-neutral-950/90 p-2 backdrop-blur-xl sm:w-48 sm:p-3">
        <div className="px-2.5 py-3 text-sm font-semibold">Bliss</div>
        <nav aria-label="Workflow" className="grid gap-1">
          <Button asChild variant={stitchSelected ? "ghost" : "secondary"} className="w-full justify-start">
            <a href="#downloader" aria-current={stitchSelected ? undefined : "page"}>Downloader</a>
          </Button>
          <Button asChild variant={stitchSelected ? "secondary" : "ghost"} className="w-full justify-start">
            <a href="#stitch" aria-current={stitchSelected ? "page" : undefined}>Stitch</a>
          </Button>
        </nav>
      </aside>
      <main hidden={stitchSelected} aria-label="Downloader" className={workspaceClass}>
        <form id="download-form" className="grid max-w-lg gap-4">
          <div className="grid gap-2">
            <Label htmlFor="series-url">Series link</Label>
            <Input id="series-url" name="url" inputMode="url" autoComplete="off"
              required maxLength={2048} placeholder="Webtoon or Weeb Central" />
          </div>
          <fieldset className="min-w-0" aria-describedby="chapter-position-note">
            <legend className="mb-3 text-sm font-medium">Chapter range</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="chapter-start">From</Label>
                <Input id="chapter-start" name="start" type="number" min={1} step={1} placeholder="First" />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="chapter-end">To</Label>
                <Input id="chapter-end" name="end" type="number" min={1} step={1} placeholder="Last" />
              </div>
            </div>
            <p id="chapter-position-note" className="mt-2 text-xs text-muted-foreground">
              Positions count from the first listed chapter.
            </p>
          </fieldset>
          <div className="flex flex-wrap gap-2">
            <Button type="submit">Download</Button>
            <Button type="button" id="open-folder" variant="outline">Open Folder</Button>
            <Button type="button" id="cancel-download" variant="ghost" disabled>Cancel</Button>
          </div>
          <p id="download-status" role="status" className="text-sm text-muted-foreground" />
        </form>
      </main>
      {stitchSelected && (
        <main aria-label="Stitch" className={workspaceClass}>
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
