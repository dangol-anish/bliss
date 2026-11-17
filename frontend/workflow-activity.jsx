import { useEffect, useState } from "react";

export function WorkflowActivity() {
  const [operations, setOperations] = useState([]);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    let previous = [];
    async function poll() {
      try {
        const response = await fetch("/api/workflow/active", {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) return;
        const active = await response.json();
        if (controller.signal.aborted) return;
        const ids = new Set(active.map(operation => operation.id));
        for (const stage of new Set(previous.filter(operation => !ids.has(operation.id))
          .map(operation => operation.stage))) {
          window.dispatchEvent(new CustomEvent("bliss-batch-complete", { detail: stage }));
        }
        previous = active;
        setOperations(active);
      } catch {
        // Retain the last known state while reconnecting.
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 750);
      }
    }
    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);

  if (!operations.length) return null;
  return <ul aria-label="Active operations" className="mt-3 grid gap-2 border-t border-white/10 px-2 pt-3 text-xs text-muted-foreground">
    {operations.map(({ id, stage, label, percent, series, chapter, end_chapter }) =>
      <li key={id}>
        <a href={`#${stage}`} className="block break-words hover:text-white">
          {label}{Number.isInteger(percent) ? ` · ${percent}%` : ""}
          {series && <span className="block">{series} · {chapter}{end_chapter && end_chapter !== chapter ? `–${end_chapter}` : ""}</span>}
        </a>
      </li>)}
  </ul>;
}
