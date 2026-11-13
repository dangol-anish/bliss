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

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (button.disabled) return;
  const entries = Array.from(rows.querySelectorAll("[data-download-row]"), row => ({
    url: row.querySelector('[name="url"]').value.trim(),
    start: row.querySelector('[name="start"]').valueAsNumber || null,
    end: row.querySelector('[name="end"]').valueAsNumber || null,
  }));
  const progressId = crypto.randomUUID();
  const progress = new AbortController();
  let timer;
  let cancelling = false;
  button.disabled = true;
  rows.disabled = true;
  status.textContent = "0%";

  async function updateProgress() {
    try {
      const response = await fetch(`/api/download/progress/${progressId}`, {
        signal: progress.signal,
      });
      if (response.ok) {
        const { percent } = await response.json();
        if (!progress.signal.aborted && !cancelling && Number.isInteger(percent)) {
          cancelButton.disabled = percent >= 100;
          status.textContent = `${percent}%`;
        }
      }
    } catch {
      // The download request itself reports any failure.
    } finally {
      if (!progress.signal.aborted) {
        timer = setTimeout(updateProgress, 750);
      }
    }
  }

  async function cancelDownload() {
    cancelling = true;
    cancelButton.disabled = true;
    status.textContent = "Cancelling…";
    try {
      const response = await fetch(`/api/download/cancel/${progressId}`, { method: "POST" });
      if (!response.ok) {
        throw new Error("Could not cancel the download.");
      }
    } catch (error) {
      if (!progress.signal.aborted) {
        cancelling = false;
        cancelButton.disabled = false;
        toast.error(error.message);
      }
    }
  }

  cancelButton.addEventListener("click", cancelDownload);
  timer = setTimeout(updateProgress, 750);

  try {
    const response = await fetch("/api/download/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries, progress_id: progressId }),
    });
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
    status.textContent = "";
    progress.abort();
    clearTimeout(timer);
    cancelButton.removeEventListener("click", cancelDownload);
    cancelButton.disabled = true;
    rows.disabled = false;
    button.disabled = false;
  }
});
