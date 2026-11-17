import { mountDownloader } from "./downloader-layout.jsx";
import { toast } from "./notifications.js";

mountDownloader();

const form = document.querySelector("#download-form");
const button = form.querySelector('button[type="submit"]');
const rows = document.querySelector("#download-rows");
const status = document.querySelector("#download-status");
const cancelButton = document.querySelector("#cancel-download");
const folderButton = document.querySelector("#open-folder");

folderButton.addEventListener("click", async () => {
  folderButton.disabled = true;
  try {
    const response = await fetch("/api/download/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.detail || "Could not open the download folder.");
    }
  } catch (error) {
    toast.error(error.message);
  } finally {
    folderButton.disabled = false;
  }
});

let currentDownload = null;

function setBusy(busy) {
  button.disabled = busy;
  rows.disabled = busy;
}

async function activeDownload() {
  const response = await fetch("/api/download/active", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not check download progress.");
  return response.json();
}

function followDownload(id, { submitting = false, snapshot = null } = {}) {
  const job = { id, submitting, cancelling: false, timer: null };
  currentDownload = job;
  setBusy(true);

  function showProgress(active) {
    status.textContent = job.cancelling || active.cancelling ? "Cancelling…" : `${active.percent}%`;
    cancelButton.disabled = job.cancelling || active.cancelling || active.percent >= 100;
  }

  async function poll() {
    try {
      const active = await activeDownload();
      if (currentDownload !== job) return;
      if (active?.id === id) {
        showProgress(active);
      } else if (!job.submitting) {
        finishDownload(job);
      }
    } catch {
      // Keep following the download if the next request succeeds.
    } finally {
      if (currentDownload === job) job.timer = setTimeout(poll, 750);
    }
  }

  if (snapshot) showProgress(snapshot);
  else status.textContent = "0%";
  job.timer = setTimeout(poll, 750);
  return job;
}

function finishDownload(job) {
  if (currentDownload !== job) return;
  currentDownload = null;
  clearTimeout(job.timer);
  status.textContent = "";
  cancelButton.disabled = true;
  setBusy(false);
}

cancelButton.addEventListener("click", async () => {
  const job = currentDownload;
  if (!job || job.cancelling) return;
  job.cancelling = true;
  cancelButton.disabled = true;
  status.textContent = "Cancelling…";
  try {
    const response = await fetch(`/api/download/cancel/${job.id}`, { method: "POST" });
    if (!response.ok && response.status !== 409) throw new Error("Could not cancel the download.");
  } catch (error) {
    toast.error(error.message);
  } finally {
    job.cancelling = false;
  }
});

setBusy(true);
activeDownload().then(active => {
  if (active) followDownload(active.id, { snapshot: active });
}).catch(() => {
  // Allow starting a download even if this initial request fails.
}).finally(() => {
  if (!currentDownload) setBusy(false);
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (button.disabled) return;

  const entries = Array.from(rows.querySelectorAll("[data-download-row]"), row => ({
    url: row.querySelector('[name="url"]').value.trim(),
    start: row.querySelector('[name="start"]').valueAsNumber || null,
    end: row.querySelector('[name="end"]').valueAsNumber || null,
  }));
  const progressId = crypto.randomUUID();
  const job = followDownload(progressId, { submitting: true });

  try {
    const response = await fetch("/api/download/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries, progress_id: progressId }),
    });
    if (response.status === 409) {
      const active = await activeDownload();
      if (active) {
        finishDownload(job);
        followDownload(active.id, { snapshot: active });
        return;
      }
    }
    const result = await response.json();
    if (!response.ok) {
      throw new Error(typeof result.detail === "string" ? result.detail : "Check the link and chapter range.");
    }
    if (result.cancelled) {
      toast.info("Download cancelled");
      return;
    }
    const label = result.chapters === 1 ? "chapter" : "chapters";
    toast.success(`${result.chapters} ${label} downloaded`, { description: result.folder || `${result.series} series` });
  } catch (error) {
    toast.error("Download failed", { description: error.message });
  } finally {
    finishDownload(job);
  }
});
