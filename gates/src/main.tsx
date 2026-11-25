import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/outfit/latin-400.css";
import "@fontsource/outfit/latin-500.css";
import "@fontsource/outfit/latin-600.css";
import "@fontsource/outfit/latin-700.css";
import "@fontsource/outfit/latin-800.css";
import "@fontsource/pixelify-sans/latin-500.css";
import "@fontsource/pixelify-sans/latin-700.css";
import "@fontsource/press-start-2p/latin-400.css";
import { App } from "./ui/App";
import "./ui/app.css";

async function bootDiscordActivity() {
  const host = window.location.hostname;
  if (!host.endsWith("discordsays.com")) return;
  const clientId = host.split(".")[0];
  if (!/^\d+$/.test(clientId)) return;
  const { DiscordSDK } = await import("@discord/embedded-app-sdk");
  const sdk = new DiscordSDK(clientId);
  await sdk.ready();
}

void bootDiscordActivity()
  .catch(() => undefined)
  .finally(() => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
  });

