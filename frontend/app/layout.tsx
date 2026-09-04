import type { Metadata, Viewport } from "next";
import "./globals.css";
import { PwaRegistration } from "./pwa";

export const metadata: Metadata = {
  title: "Zenith — Your day, in focus",
  description: "A calm, local-first personal manager.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg", apple: "/icon.svg" },
  appleWebApp: { capable: true, title: "Zenith", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = { themeColor: "#171916" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body><PwaRegistration />{children}</body>
    </html>
  );
}
