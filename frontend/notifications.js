import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { Toaster, toast as sonnerToast } from "sonner";

createRoot(document.querySelector("#notifications")).render(
  createElement(Toaster, {
    position: "bottom-right",
    theme: "dark",
    style: {
      "--normal-bg": "#0a0a0a",
      "--normal-text": "#fafafa",
      "--normal-border": "#262626",
    },
  }),
);

let audio;

function unlockAudio() {
  if (!window.AudioContext) return;
  audio ??= new AudioContext();
  if (audio.state === "suspended") audio.resume().catch(() => {});
}

window.addEventListener("pointerdown", unlockAudio, { capture: true });
window.addEventListener("keydown", unlockAudio, { capture: true });

function ring(notes) {
  if (audio?.state !== "running") return;
  const start = audio.currentTime;
  for (const [index, frequency] of notes.entries()) {
    const oscillator = audio.createOscillator();
    const envelope = audio.createGain();
    const time = start + index * 0.13;
    oscillator.frequency.value = frequency;
    envelope.gain.setValueAtTime(0.001, time);
    envelope.gain.exponentialRampToValueAtTime(0.12, time + 0.015);
    envelope.gain.exponentialRampToValueAtTime(0.001, time + 0.25);
    oscillator.connect(envelope).connect(audio.destination);
    oscillator.start(time);
    oscillator.stop(time + 0.25);
  }
}

export const toast = {
  success(...args) {
    ring([660, 880]);
    return sonnerToast.success(...args);
  },
  error(...args) {
    ring([440, 294]);
    return sonnerToast.error(...args);
  },
  info(...args) {
    ring([550]);
    return sonnerToast.info(...args);
  },
};
