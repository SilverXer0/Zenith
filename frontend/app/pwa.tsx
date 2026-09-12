"use client";

import { useEffect, useState } from "react";

type InstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

export function PwaRegistration() {
  useEffect(() => {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);
  return null;
}

export function InstallButton() {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null);
  const [ios, setIos] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [showIosHelp, setShowIosHelp] = useState(false);

  useEffect(() => {
    const userAgent = navigator.userAgent || "";
    setIos(/iPhone|iPad|iPod/.test(userAgent) || (userAgent.includes("Macintosh") && navigator.maxTouchPoints > 1));
    setInstalled(window.matchMedia("(display-mode: standalone)").matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone));
    const capture = (event: Event) => {
      event.preventDefault();
      setPrompt(event as InstallPrompt);
    };
    window.addEventListener("beforeinstallprompt", capture);
    return () => window.removeEventListener("beforeinstallprompt", capture);
  }, []);

  async function install() {
    if (!prompt) return;
    await prompt.prompt();
    await prompt.userChoice;
    setPrompt(null);
  }

  if (installed) return null;
  if (prompt) return <button className="quiet-button" type="button" onClick={() => void install()}>Install Zenith</button>;
  if (!ios) return null;
  return <div className="flex items-center gap-2"><button className="quiet-button" type="button" onClick={() => setShowIosHelp((value) => !value)}>Install Zenith</button>{showIosHelp && <span className="muted text-xs">Tap Share, then Add to Home Screen.</span>}</div>;
}
