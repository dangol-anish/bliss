import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { Toaster, toast } from "sonner";

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

export { toast };
