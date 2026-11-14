export async function batchRequest(path, body, signal) {
  const response = await fetch(path, {
    cache: "no-store",
    ...(body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    signal,
  });
  if (response.status === 204) return null;
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the selection and settings.");
  return result;
}

export async function runWithProgress(path, payload, progressPath, report) {
  const progress_id = crypto.randomUUID();
  const controller = new AbortController();
  let timer;
  async function poll() {
    try {
      const { percent } = await batchRequest(`${progressPath}/${progress_id}`, undefined, controller.signal);
      if (!controller.signal.aborted && Number.isInteger(percent)) report(percent);
    } catch {
      // The operation reports its own failures.
    } finally {
      if (!controller.signal.aborted) timer = setTimeout(poll, 750);
    }
  }
  timer = setTimeout(poll, 400);
  try {
    return await batchRequest(path, { ...payload, progress_id });
  } finally {
    controller.abort();
    clearTimeout(timer);
  }
}

export const batchSections = {
  stitch: { catalog: "stitch", actions: [["renumber", "Renumber"], ["create", "Stitch"]] },
  panels: { catalog: "panels", actions: [["detect", "Detect Panels"]], perChapter: true },
  "split-strip": { catalog: "split", actions: [["split", "Split & Save"]], perChapter: true },
  narration: { catalog: "narration", actions: [["narrate", "Start Narration"]], perChapter: true },
  preview: { catalog: "preview", actions: [["render", "Render Previews"]] },
  backup: { catalog: "stitch", actions: [["backup", "Move to Backup"]] },
};

export async function runBatchItem(action, item, settings, report, stopped) {
  const query = new URLSearchParams(item);
  switch (action) {
    case "renumber":
      await batchRequest("/api/stitch/renumber", item);
      return;
    case "create":
      await runWithProgress("/api/stitch/create", item, "/api/stitch/progress", report);
      return;
    case "detect": {
      const document = await batchRequest(`/api/panels/chapter?${query}`);
      await runWithProgress("/api/panels/detect", { ...item, source: document.source }, "/api/panels/progress", report);
      return;
    }
    case "split": {
      const payload = { ...item, ...settings };
      const plan = await batchRequest("/api/split/preview", payload);
      if (!stopped()) await batchRequest("/api/split/save", { ...payload, preview_id: plan.id });
      return;
    }
    case "render": {
      const details = await batchRequest(`/api/preview/chapter?${query}`);
      if (stopped()) return;
      if (!details.ready) {
        await runWithProgress("/api/preview/generate", { ...item, key: details.key }, "/api/preview/progress", report);
      }
      query.set("preview", details.key);
      return { watch: `/?${query}#preview`, download: `/api/preview/video/${details.key}?${new URLSearchParams(item)}&download=true` };
    }
    case "narrate": {
      const details = await batchRequest(`/api/narration/chapter?${query}`);
      if (details.accepted || stopped()) return;
      if (details.submitted) throw new Error("Check the last AI Studio request in Narration before retrying.");
      await batchRequest("/api/narration/start", { ...item, key: details.key, revision: details.revision });
      while (true) {
        if (stopped()) {
          await batchRequest("/api/narration/stop", {});
          return;
        }
        const job = await batchRequest("/api/narration/job");
        if (job?.key !== details.key) throw new Error("The narration job changed. Check Narration.");
        if (!job.running) {
          if (job.status === "complete") return;
          const error = new Error(job.message || "Narration paused.");
          error.alertPlayed = job.status === "paused";
          throw error;
        }
        report(100 * job.completed / job.total);
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
  }
}
